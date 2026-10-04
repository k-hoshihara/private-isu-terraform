CREATE USER IF NOT EXISTS 'isuconp'@'10.42.%' IDENTIFIED BY 'isuconp';
GRANT ALL PRIVILEGES ON isuconp.* TO 'isuconp'@'10.42.%';
FLUSH PRIVILEGES;
SELECT user, host FROM mysql.user WHERE user = 'isuconp';
