provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "aegis-voice-guide"
      Environment = "portfolio"
      ManagedBy   = "terraform"
      Owner       = "abhishek"
    }
  }
}
