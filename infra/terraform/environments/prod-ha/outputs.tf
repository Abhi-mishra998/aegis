output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "rds_endpoint" {
  value     = module.rds.endpoint
  sensitive = true
}

output "redis_primary_endpoint" {
  value     = module.redis.primary_endpoint
  sensitive = true
}

output "redis_reader_endpoint" {
  value     = module.redis.reader_endpoint
  sensitive = true
}

output "asg_name" {
  value = module.asg.asg_name
}

output "waf_web_acl_arn" {
  value = module.waf.web_acl_arn
}

output "nat_gateway_public_ips" {
  description = "Pin these in vendor allowlists for egress from prod-ha."
  value       = module.network.nat_gateway_public_ips
}

output "vpc_id" {
  value = module.network.vpc_id
}
