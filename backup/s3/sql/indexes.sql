ALTER TABLE comments ADD INDEX idx_comments_post_created (post_id, created_at);
