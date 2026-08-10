data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ssm" {
  name               = "${var.name_prefix}-ssm"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

# 書籍では AmazonEC2RoleforSSM だが、こちらは AWS 側で非推奨になっている。
# 現在の推奨は AmazonSSMManagedInstanceCore。
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  name = "${var.name_prefix}-ssm"
  role = aws_iam_role.ssm.name
}
