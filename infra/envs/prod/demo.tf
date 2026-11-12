# ---------- demo business host (task 6.15) ----------
# Kitebase, the fictional business the demo reconciles, runs on its own small instance so it
# shares nothing with SettleIQ but the network. Its database is reachable only from the SettleIQ
# host, whose merchant connectors read and correct it; the public gets HTTP(S) to the shop only.
#
# Its runtime values live under /settleiq/demo. Terraform creates no placeholders there:
# infra/push-demo-env.sh creates the parameters, so they can be set before or after this applies.

locals {
  demo_name       = "${local.name}-demo"
  demo_param_path = "/settleiq/demo"
}

resource "aws_security_group" "demo" {
  name        = local.demo_name
  description = "Kitebase demo shop; Postgres from the SettleIQ host only; no SSH (access is via SSM)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (ACME + redirect)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description     = "Postgres for the SettleIQ merchant connectors, over the VPC"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "demo_host" {
  name               = "${local.demo_name}-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "demo_host_ssm" {
  role       = aws_iam_role.demo_host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "demo_host_inline" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullDemoImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.svc["demo"].arn]
  }
  statement {
    sid       = "ReadDemoReleases"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.releases.arn}/demo-releases/*"]
  }
  statement {
    sid       = "ListDemoReleases"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.releases.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["demo-releases/*"]
    }
  }
  statement {
    sid     = "ReadDemoConfig"
    actions = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.demo_param_path}",
      "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.demo_param_path}/*",
    ]
  }
}

resource "aws_iam_role_policy" "demo_host_inline" {
  role   = aws_iam_role.demo_host.id
  policy = data.aws_iam_policy_document.demo_host_inline.json
}

resource "aws_iam_instance_profile" "demo_host" {
  name = "${local.demo_name}-host"
  role = aws_iam_role.demo_host.name
}

# demo-deploy.yml finds this instance by its Name tag, so replacing it needs no variable change.
resource "aws_instance" "demo" {
  ami                    = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type          = var.demo_instance_type
  subnet_id              = data.aws_subnets.in_az.ids[0]
  vpc_security_group_ids = [aws_security_group.demo.id]
  iam_instance_profile   = aws_iam_instance_profile.demo_host.name

  credit_specification {
    cpu_credits = var.cpu_credits
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers cannot reach instance credentials
  }

  # The shop's data is disposable: Reset discrepancies rebuilds it, so it needs no own volume.
  root_block_device {
    volume_size = 12
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/demo_user_data.sh.tftpl", {
    region         = var.region
    registry       = local.registry
    param_path     = "${local.demo_param_path}/"
    release_bucket = aws_s3_bucket.releases.bucket
  })

  tags = {
    Name = local.demo_name
  }

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "demo" {
  domain   = "vpc"
  instance = aws_instance.demo.id
  tags = {
    Name = local.demo_name
  }
}

resource "aws_route53_record" "demo" {
  count   = var.route53_zone_id == "" ? 0 : 1
  zone_id = var.route53_zone_id
  name    = var.demo_domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.demo.public_ip]
}
