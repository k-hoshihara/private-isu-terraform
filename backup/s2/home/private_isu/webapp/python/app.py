import datetime
import hashlib  # ← 追加
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import time
import urllib.parse

import flask
import MySQLdb.cursors
from jinja2 import pass_eval_context
from markupsafe import Markup, escape
from pymemcache.client.base import Client as MemcacheClient

UPLOAD_LIMIT = 10 * 1024 * 1024  # 10mb
POSTS_PER_PAGE = 20


_config = None


def config():
    global _config
    if _config is None:
        _config = {
            "db": {
                "host": os.environ.get("ISUCONP_DB_HOST", "localhost"),
                "port": int(os.environ.get("ISUCONP_DB_PORT", "3306")),
                "user": os.environ.get("ISUCONP_DB_USER", "root"),
                "db": os.environ.get("ISUCONP_DB_NAME", "isuconp"),
            },
            "memcache": {
                "address": os.environ.get(
                    "ISUCONP_MEMCACHED_ADDRESS", "127.0.0.1:11211"
                ),
            },
        }
        password = os.environ.get("ISUCONP_DB_PASSWORD")
        if password:
            _config["db"]["passwd"] = password
    return _config


_db = None


# OperationalError で _db = None にして ping/connect をやり直す
_db_ping_at = 0.0


def db():
    global _db, _db_ping_at
    last = None
    for _ in range(30):
        try:
            if _db is None:
                conf = config()["db"].copy()
                conf["charset"] = "utf8mb4"
                conf["cursorclass"] = MySQLdb.cursors.DictCursor
                conf["autocommit"] = True
                conf["connect_timeout"] = 5
                _db = MySQLdb.connect(**conf)
            # ping は毎回の往復になるので 1 秒に 1 回まで。DB 起動待ちの再接続は保つ
            now = time.monotonic()
            if now - _db_ping_at > 1.0:
                _db.ping(True)
                _db_ping_at = now
            return _db
        except MySQLdb.OperationalError as e:
            last = e
            _db = None
            time.sleep(1)
    raise last


def db_initialize():
    cur = db().cursor()
    sqls = [
        "DELETE FROM users WHERE id > 1000",
        "DELETE FROM posts WHERE id > 10000",
        "DELETE FROM comments WHERE id > 100000",
        "UPDATE users SET del_flg = 0",
        "UPDATE users SET del_flg = 1 WHERE id % 50 = 0",
    ]
    for q in sqls:
        cur.execute(q)
    # ベンチ初期化でコメントが消えるため、件数の列を作り直す
    cur.execute(
        "UPDATE posts p SET p.comment_count = "
        "(SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id)"
    )
    _user_cache.clear()
    _post_cache.clear()


_mcclient = None


def memcache():
    global _mcclient
    if _mcclient is None:
        conf = config()["memcache"]
        _mcclient = MemcacheClient(
            conf["address"], no_delay=True, default_noreply=False
        )
    return _mcclient


def try_login(account_name, password):
    cur = db().cursor()
    cur.execute(
        "SELECT * FROM users WHERE account_name = %s AND del_flg = 0", (account_name,)
    )
    user = cur.fetchone()

    if user and calculate_passhash(user["account_name"], password) == user["passhash"]:
        return user
    return None


def validate_user(account_name: str, password: str):
    if not re.fullmatch(r"[0-9a-zA-Z_]{3,}", account_name):
        return False
    if not re.fullmatch(r"[0-9a-zA-Z_]{6,}", password):
        return False
    return True


def digest(src: str):
    return hashlib.sha512(src.encode("utf-8")).hexdigest()


def calculate_salt(account_name: str):
    return digest(account_name)


def calculate_passhash(account_name: str, password: str):
    return digest("%s:%s" % (password, calculate_salt(account_name)))


_user_cache = {}


def get_session_user():
    user = flask.session.get("user")
    if not user:
        return None
    hit = _user_cache.get(user["id"])
    if hit is not None:
        return hit
    cur = db().cursor()
    cur.execute("SELECT * FROM `users` WHERE `id` = %s", (user["id"],))
    row = cur.fetchone()
    if row is not None:
        _user_cache[user["id"]] = row
    return row


_post_cache = {}


def _cache_put(cache, key, value, limit):
    """上限付きで入れる。上限を超えたら古いものから捨てる（メモリを食い切らないため）。"""
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _get_users(user_ids):
    """id ごとのユーザー行を返す。メモリに無い id だけ DB へ行く。"""
    missing = [uid for uid in user_ids if uid not in _user_cache]
    if missing:
        cur = db().cursor()
        marks = ",".join(["%s"] * len(missing))
        cur.execute(
            "SELECT `id`, `account_name`, `authority`, `del_flg`, `created_at` "
            f"FROM `users` WHERE `id` IN ({marks})",
            missing,
        )
        for row in cur:
            _cache_put(_user_cache, row["id"], row, 2000)
    return {uid: _user_cache[uid] for uid in user_ids if uid in _user_cache}


def make_posts(results, all_comments=False):
    posts = []
    if not results:
        return posts
    cursor = db().cursor()
    users = {}
    chunk_size = 200

    for start in range(0, len(results), chunk_size):
        chunk_posts = results[start : start + chunk_size]
        post_ids = [post["id"] for post in chunk_posts]
        marks = ",".join(["%s"] * len(post_ids))
        if all_comments:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` "
                f"FROM `comments` WHERE `post_id` IN ({marks}) "
                "ORDER BY `created_at` DESC"
            )
        else:
            query = (
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at` FROM ("
                "SELECT `id`, `post_id`, `user_id`, `comment`, `created_at`, "
                "ROW_NUMBER() OVER (PARTITION BY `post_id` ORDER BY `created_at` DESC) AS rn "
                f"FROM `comments` WHERE `post_id` IN ({marks})"
                ") AS ranked WHERE rn <= 3 ORDER BY `created_at` DESC"
            )
        cursor.execute(query, post_ids)
        comments_by_post = {}
        user_ids = set()
        for comment in cursor:
            comments_by_post.setdefault(comment["post_id"], []).append(comment)
            user_ids.add(comment["user_id"])

        user_ids.update(post["user_id"] for post in chunk_posts)
        user_ids.difference_update(users)
        if user_ids:
            users.update(_get_users(user_ids))

        for post in chunk_posts:
            post["user"] = users[post["user_id"]]
            if post["user"]["del_flg"]:
                continue
            comments = comments_by_post.get(post["id"], [])
            for comment in comments:
                comment["user"] = users[comment["user_id"]]
            post["comments"] = list(reversed(comments))
            posts.append(post)
            if len(posts) >= POSTS_PER_PAGE:
                return posts
    return posts


# app setup
static_path = pathlib.Path(__file__).resolve().parent.parent / "public"
app = flask.Flask(__name__, static_folder=str(static_path), static_url_path="")
# app.debug = True

# Flask-Session
# セッションは署名付き cookie（Flask 既定）で持つ。memcached への往復をやめる
app.config["SECRET_KEY"] = "isuconp-tutorial-secret"


@app.template_global()
def image_url(post):
    ext = ""
    mime = post["mime"]
    if mime == "image/jpeg":
        ext = ".jpg"
    elif mime == "image/png":
        ext = ".png"
    elif mime == "image/gif":
        ext = ".gif"

    return "/image/%s%s" % (post["id"], ext)


# http://flask.pocoo.org/snippets/28/
_paragraph_re = re.compile(r"(?:\r\n|\r|\n){2,}")


@app.template_filter()
@pass_eval_context
def nl2br(eval_ctx, value):
    result = "\n\n".join(
        "<p>%s</p>" % p.replace("\n", "<br>\n")
        for p in _paragraph_re.split(escape(value))
    )
    if eval_ctx.autoescape:
        result = Markup(result)
    return result


def _nl2br(value):
    result = "\n\n".join(
        "<p>%s</p>" % p.replace("\n", "<br>\n")
        for p in _paragraph_re.split(escape(value))
    )
    return Markup(result)


def _post_html(post, csrf_token):
    user = post["user"]
    account = user["account_name"]
    account_q = urllib.parse.quote(str(account), safe="/")
    comments = "\n".join(
        "    <div class=\"isu-comment\">\n"
        f'      <a href="/@{urllib.parse.quote(str(c["user"]["account_name"]), safe="/")}" '
        f'class="isu-comment-account-name">{escape(c["user"]["account_name"])}</a>\n'
        f'      <span class="isu-comment-text">{escape(c["comment"])}</span>\n'
        "    </div>"
        for c in post["comments"]
    )
    return (
        f'<div class="isu-post" id="pid_{escape(post["id"])}" '
        f'data-created-at="{escape(post["created_at"])}">\n'
        "  <div class=\"isu-post-header\">\n"
        f'    <a href="/@{account_q}" class="isu-post-account-name">{escape(account)}</a>\n'
        f'    <a href="/posts/{escape(post["id"])}" class="isu-post-permalink">\n'
        f'      <time class="timeago" datetime="{escape(post["created_at"])}"></time>\n'
        "    </a>\n"
        "  </div>\n"
        "  <div class=\"isu-post-image\">\n"
        f'    <img src="{escape(image_url(post))}" class="isu-image">\n'
        "  </div>\n"
        "  <div class=\"isu-post-text\">\n"
        f'    <a href="/@{account_q}" class="isu-post-account-name">{escape(account)}</a>\n'
        f"    {_nl2br(post['body'])}\n"
        "  </div>\n"
        "  <div class=\"isu-post-comment\">\n"
        "    <div class=\"isu-post-comment-count\">\n"
        f"      comments: <b>{escape(post['comment_count'])}</b>\n"
        "    </div>\n"
        "\n"
        f"{comments}\n"
        "    <div class=\"isu-comment-form\">\n"
        '      <form method="post" action="/comment">\n'
        '        <input type="text" name="comment">\n'
        f'        <input type="hidden" name="post_id" value="{escape(post["id"])}">\n'
        f'        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">\n'
        '        <input type="submit" name="submit" value="submit">\n'
        "      </form>\n"
        "    </div>\n"
        "  </div>\n"
        "</div>"
    )


def render_posts_html(posts, csrf_token):
    body = "\n".join(_post_html(p, csrf_token) for p in posts)
    return Markup('<div class="isu-posts">\n' + body + "\n</div>")


# endpoints


@app.route("/initialize")
def get_initialize():
    db_initialize()
    return ""


@app.route("/login")
def get_login():
    if get_session_user():
        return flask.redirect("/")
    return flask.render_template("login.html", me=None)


@app.route("/login", methods=["POST"])
def post_login():
    if get_session_user():
        return flask.redirect("/")

    user = try_login(flask.request.form["account_name"], flask.request.form["password"])
    if user:
        flask.session["user"] = {"id": user["id"]}
        flask.session["csrf_token"] = os.urandom(16).hex()
        return flask.redirect("/")

    flask.flash("アカウント名かパスワードが間違っています")
    return flask.redirect("/login")


@app.route("/register")
def get_register():
    if get_session_user():
        return flask.redirect("/")
    return flask.render_template("register.html", me=None)


@app.route("/register", methods=["POST"])
def post_register():
    if get_session_user():
        return flask.redirect("/")

    account_name = flask.request.form["account_name"]
    password = flask.request.form["password"]
    if not validate_user(account_name, password):
        flask.flash(
            "アカウント名は3文字以上、パスワードは6文字以上である必要があります"
        )
        return flask.redirect("/register")

    cursor = db().cursor()
    cursor.execute("SELECT 1 FROM users WHERE `account_name` = %s", (account_name,))
    user = cursor.fetchone()
    if user:
        flask.flash("アカウント名がすでに使われています")
        return flask.redirect("/register")

    query = "INSERT INTO `users` (`account_name`, `passhash`) VALUES (%s, %s)"
    cursor.execute(query, (account_name, calculate_passhash(account_name, password)))

    flask.session["user"] = {"id": cursor.lastrowid}
    flask.session["csrf_token"] = os.urandom(16).hex()
    return flask.redirect("/")


@app.route("/logout")
def get_logout():
    flask.session.clear()
    return flask.redirect("/")


@app.route("/")
def get_index():
    me = get_session_user()
    cursor = db().cursor()
    cursor.execute(
        "SELECT p.`id`, p.`user_id`, p.`body`, "
        "CAST(p.`created_at` AS CHAR) AS `created_at`, "
        "p.`mime`, p.`comment_count` "
        "FROM `posts` p FORCE INDEX (idx_posts_created) "
        "JOIN `users` u ON u.`id` = p.`user_id` "
        "WHERE u.`del_flg` = 0 ORDER BY p.`created_at` DESC LIMIT 20"
    )
    posts = make_posts(cursor.fetchall())
    return flask.render_template(
        "index.html",
        posts_html=render_posts_html(posts, flask.session.get("csrf_token")),
        me=me,
    )


@app.route("/@<account_name>")
def get_user_list(account_name):
    cursor = db().cursor()

    cursor.execute(
        "SELECT * FROM `users` WHERE `account_name` = %s AND `del_flg` = 0",
        (account_name,),
    )
    user = cursor.fetchone()
    if user is None:
        flask.abort(404)  # raises exception

    cursor.execute(
        "SELECT `id`, `user_id`, `body`, `mime`, `created_at`, `comment_count` "
        "FROM `posts` WHERE `user_id` = %s ORDER BY `created_at` DESC",
        (user["id"],),
    )
    posts = make_posts(cursor.fetchall())

    cursor.execute(
        "SELECT COUNT(*) AS count FROM `comments` WHERE `user_id` = %s", (user["id"],)
    )
    comment_count = cursor.fetchone()["count"]

    cursor.execute(
        "SELECT COUNT(*) AS post_count, COALESCE(SUM(`comment_count`), 0) AS commented_count "
        "FROM `posts` WHERE `user_id` = %s",
        (user["id"],),
    )
    row = cursor.fetchone()
    post_count = row["post_count"]
    commented_count = row["commented_count"]

    me = get_session_user()

    return flask.render_template(
        "user.html",
        posts_html=render_posts_html(posts, flask.session.get("csrf_token")),
        user=user,
        post_count=post_count,
        comment_count=comment_count,
        commented_count=commented_count,
        me=me,
    )


def _parse_iso8601(s):
    # http://bugs.python.org/issue15873
    # Ignore timezone
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ tT](\d{2}):(\d{2}):(\d{2}).*", s)
    if not m:
        raise ValueError("Invlaid iso8601 format: %r" % (s,))
    return datetime.datetime(*map(int, m.groups()))


@app.route("/posts")
def get_posts():
    cursor = db().cursor()
    max_created_at = flask.request.args.get("max_created_at")
    if not max_created_at:
        return ""

    max_created_at = _parse_iso8601(max_created_at)
    cursor.execute(
        "SELECT p.`id`, p.`user_id`, p.`body`, p.`mime`, "
        "CAST(p.`created_at` AS CHAR) AS `created_at`, "
        "p.`comment_count` "
        "FROM `posts` p FORCE INDEX (idx_posts_created) "
        "JOIN `users` u ON u.`id` = p.`user_id` "
        "WHERE p.`created_at` <= %s AND u.`del_flg` = 0 "
        "ORDER BY p.`created_at` DESC LIMIT 20",
        (max_created_at,),
    )
    results = cursor.fetchall()
    posts = make_posts(results)
    return flask.render_template(
        "posts.html",
        posts_html=render_posts_html(posts, flask.session.get("csrf_token")),
    )


@app.route("/posts/<id>")
def get_posts_id(id):
    hit = _post_cache.get(id)
    if hit is not None:
        posts = hit
    else:
        cursor = db().cursor()
        cursor.execute("SELECT * FROM `posts` WHERE `id` = %s", (id,))
        posts = make_posts(cursor.fetchall(), all_comments=True)
        if posts:
            _cache_put(_post_cache, id, posts, 128)
    if not posts:
        flask.abort(404)

    me = get_session_user()
    return flask.render_template("post.html", post=posts[0], me=me)


@app.route("/", methods=["POST"])
def post_index():
    me = get_session_user()
    if not me:
        return flask.redirect("/login")

    if flask.request.form["csrf_token"] != flask.session["csrf_token"]:
        flask.abort(422)

    file = flask.request.files.get("file")
    if not file:
        flask.flash("画像が必要です")
        return flask.redirect("/")

    # 投稿のContent-Typeからファイルのタイプを決定する
    mime = file.mimetype
    if mime not in ("image/jpeg", "image/png", "image/gif"):
        flask.flash("投稿できる画像形式はjpgとpngとgifだけです")
        return flask.redirect("/")

    with tempfile.TemporaryFile() as tempf:
        file.save(tempf)
        tempf.flush()

        if tempf.tell() > UPLOAD_LIMIT:
            flask.flash("ファイルサイズが大きすぎます")
            return flask.redirect("/")

        tempf.seek(0)
        imgdata = tempf.read()

    query = "INSERT INTO `posts` (`user_id`, `mime`, `imgdata`, `body`) VALUES (%s,%s,%s,%s)"
    cursor = db().cursor()
    cursor.execute(query, (me["id"], mime, imgdata, flask.request.form.get("body")))
    pid = cursor.lastrowid
    return flask.redirect("/posts/%d" % pid)


IMAGE_DIR = pathlib.Path(__file__).resolve().parent.parent / "public" / "image"
IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


@app.route("/image/<id>.<ext>")
def get_image(id, ext):
    if not id:
        return ""
    id = int(id)
    if id == 0:
        return ""
    if ext in IMAGE_MIME:
        fp = IMAGE_DIR / f"{id}.{ext}"
        if fp.is_file():
            return flask.Response(fp.read_bytes(), mimetype=IMAGE_MIME[ext])

    cursor = db().cursor()
    cursor.execute("SELECT * FROM `posts` WHERE `id` = %s", (id,))
    post = cursor.fetchone()

    mime = post["mime"]
    if (
        ext == "jpg"
        and mime == "image/jpeg"
        or ext == "png"
        and mime == "image/png"
        or ext == "gif"
        and mime == "image/gif"
    ):
        return flask.Response(post["imgdata"], mimetype=mime)

    flask.abort(404)


@app.route("/comment", methods=["POST"])
def post_comment():
    me = get_session_user()
    if not me:
        return flask.redirect("/login")

    if flask.request.form["csrf_token"] != flask.session["csrf_token"]:
        flask.abort(422)

    post_id = flask.request.form["post_id"]
    if not re.match(r"[0-9]+", post_id):
        return "post_idは整数のみです"
    post_id = int(post_id)

    query = (
        "INSERT INTO `comments` (`post_id`, `user_id`, `comment`) VALUES (%s, %s, %s)"
    )
    cursor = db().cursor()
    cursor.execute(query, (post_id, me["id"], flask.request.form["comment"]))
    cursor.execute(
        "UPDATE `posts` SET `comment_count` = `comment_count` + 1 WHERE `id` = %s",
        (post_id,),
    )
    _post_cache.pop(str(post_id), None)

    return flask.redirect("/posts/%d" % post_id)


@app.route("/admin/banned")
def get_banned():
    me = get_session_user()
    if not me:
        return flask.redirect("/login")

    if me["authority"] == 0:
        flask.abort(403)

    cursor = db().cursor()
    cursor.execute(
        "SELECT * FROM `users` WHERE `authority` = 0 AND `del_flg` = 0 ORDER BY `created_at` DESC"
    )
    users = cursor.fetchall()

    flask.render_template("banned.html", users=users, me=me)


@app.route("/admin/banned", methods=["POST"])
def post_banned():
    me = get_session_user()
    if not me:
        return flask.redirect("/login")

    if me["authority"] == 0:
        flask.abort(403)

    if flask.request.form["csrf_token"] != flask.session["csrf_token"]:
        flask.abort(422)

    cursor = db().cursor()
    query = "UPDATE `users` SET `del_flg` = %s WHERE `id` = %s"
    for id in flask.request.form.getlist("uid", type=int):
        cursor.execute(query, (1, id))
    _user_cache.clear()
    _post_cache.clear()

    return flask.redirect("/admin/banned")
