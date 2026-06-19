# Management-events CloudTrail trail.
#
# Captures every console + API mutation against the AWS account
# (IAM changes, KMS key disable, S3 bucket policy edits, etc.).
# Multi-region = true so EU/US activity is captured if the account
# ever uses other regions.
#
# Data events (S3 object reads, Lambda invokes) are NOT enabled here —
# they are expensive at scale and noisy. Add per-bucket data events
# if a Sev-0 incident requires per-object provenance.

resource "aws_cloudtrail" "mgmt_events" {
  name           = "${var.name_prefix}-mgmt-events"
  s3_bucket_name = var.cloudtrail_bucket_name

  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true

  # Default management-events selector includes Read + Write.
  # That's the right default for incident-response.

  tags = {
    Name = "${var.name_prefix}-mgmt-events"
  }
}
