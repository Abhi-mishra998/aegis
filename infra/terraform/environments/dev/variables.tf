variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "dev_hostname" {
  description = "Public hostname for the dev portal — e.g. dev.aegisagent.in"
  type        = string
  default     = "dev.aegisagent.in"
}

variable "route53_zone_id" {
  description = "Existing Route53 hosted zone ID for aegisagent.in"
  type        = string
  default     = "Z033117538JKIIKDBDPUJ"
}

variable "bucket_suffix" {
  description = "Suffix appended to S3 bucket names to keep them globally unique. Use the AWS account ID short form or a random 8-char string."
  type        = string
  default     = "628478"
}

variable "ssh_allowed_cidrs" {
  description = "Operator IPs allowed to SSH. Default empty disables SSH; use SSM Session Manager instead."
  type        = list(string)
  default     = []
}

variable "ec2_key_name" {
  description = "EC2 SSH key pair name (only meaningful if ssh_allowed_cidrs is non-empty)"
  type        = string
  default     = ""
}

variable "budget_alert_emails" {
  description = "Email addresses notified when the dev environment exceeds 80% / 100% of the $60 monthly cap"
  type        = list(string)
  default     = ["abhishekmishra09896@gmail.com"]
}
