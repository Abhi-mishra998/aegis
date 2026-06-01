output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "prod_url" {
  value = "https://aegisagent.in"
}

output "ec2_instance_ids" {
  value = module.compute.instance_ids
}

output "rds_address" {
  value     = module.rds.address
  sensitive = true
}

output "redis_endpoint" {
  value     = module.elasticache.primary_endpoint
  sensitive = true
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "secret_names" {
  value = module.secrets.secret_names
}
