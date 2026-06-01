# Generate a fresh RSA-4096 keypair, register the public key with EC2, and
# write the private key to infrastructure/aegis-voice-guide.pem (0400, gitignored).

resource "tls_private_key" "agent" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "agent" {
  key_name   = "${local.name_prefix}-key"
  public_key = tls_private_key.agent.public_key_openssh
}

resource "local_sensitive_file" "private_key_pem" {
  content         = tls_private_key.agent.private_key_pem
  filename        = "${path.module}/${var.project_name}.pem"
  file_permission = "0400"
}
