variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "route53_zone_id" {
  description = "Existing Route53 hosted zone ID for aegisagent.in"
  type        = string
  default     = "Z033117538JKIIKDBDPUJ"
}

variable "ssh_allowed_cidrs" {
  description = "Operator IPs allowed to SSH. Live state has 49.206.52.53/32 + 0.0.0.0/0 (the latter is a security risk that should be tightened post-import)."
  type        = list(string)
  default     = ["49.206.52.53/32"]
}

variable "ec2_instance_type" {
  description = "t3.2xlarge matches live; t3.large or t3.xlarge would save ~$150-$200/month and likely still be enough"
  type        = string
  default     = "t3.2xlarge"
}

variable "ec2_key_name" {
  description = "EC2 SSH key pair name"
  type        = string
  default     = ""
}

variable "budget_alert_emails" {
  description = "Email addresses notified when prod exceeds 80%/100% of the $500 monthly cap"
  type        = list(string)
  default     = ["abhishekmishra09896@gmail.com"]
}
