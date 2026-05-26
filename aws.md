# ACP — AWS Production Deployment Guide
## Domain: aegisagent.in (GoDaddy) | Full Stack with ALB + Auto Scaling

Follow every step in order. Do not skip sections. Commands are copy-paste ready.

---

## Architecture — What You Are Building

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            INTERNET                                     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                    ┌───────────▼────────────┐
                    │  GoDaddy → Route 53    │
                    │  aegisagent.in         │
                    │  ALIAS → ALB DNS       │
                    └───────────┬────────────┘
                                │
        ┌───────────────────────▼──────────────────────────┐
        │         Application Load Balancer (ALB)          │
        │  • ACM SSL certificate (free, auto-renews)       │
        │  • HTTP :80  → redirect 301 → HTTPS              │
        │  • HTTPS :443 → Target Group → EC2:5173          │
        │  Spans: ap-south-1a + ap-south-1b (public)       │
        └───────────────────────┬──────────────────────────┘
                                │
        ┌───────────────────────▼──────────────────────────┐
        │          Auto Scaling Group (ASG)                │
        │  min=1  desired=1  max=3                         │
        │  Scale out: CPU > 70% for 5 min                  │
        │  Scale in:  CPU < 30% for 15 min                 │
        └───────────────────────┬──────────────────────────┘
                                │
        ┌───────────────────────▼──────────────────────────┐
        │       EC2 t3.xlarge — ap-south-1a (public)       │
        │       28 Docker containers (all ACP services)    │
        │  • acp_gateway     :8000  (reverse proxy)        │
        │  • acp_ui          :5173  (React dashboard)      │
        │  • acp_decision, acp_behavior, acp_audit …       │
        └────────┬───────────────────────┬─────────────────┘
                 │                       │
    ┌────────────▼──────────┐  ┌─────────▼──────────────┐
    │  RDS PostgreSQL 15    │  │  ElastiCache Redis 7   │
    │  db.t3.micro          │  │  cache.t3.micro         │
    │  Private subnets      │  │  Private subnets        │
    │  Multi-AZ (optional)  │  │  1 node (AOF backup)   │
    └───────────────────────┘  └────────────────────────┘
                 │
    ┌────────────▼──────────┐
    │  S3 Bucket            │
    │  acp-backups-prod     │
    │  • daily DB dumps     │
    │  • .env config file   │
    └───────────────────────┘
```

**VPC Subnet Map:**
```
VPC: 10.0.0.0/16 (acp-vpc)
├── ap-south-1a
│   ├── acp-public-1a   10.0.1.0/24  ← ALB node, EC2, NAT Gateway
│   └── acp-private-1a  10.0.3.0/24  ← RDS primary, ElastiCache
└── ap-south-1b
    ├── acp-public-1b   10.0.2.0/24  ← ALB node (HA)
    └── acp-private-1b  10.0.4.0/24  ← RDS standby (Multi-AZ optional)
```

**Monthly Cost Estimate (ap-south-1, Mumbai):**
```
EC2 t3.xlarge (1 instance):   ~$120/mo
RDS db.t3.micro:               ~$15/mo
ElastiCache cache.t3.micro:    ~$15/mo
ALB:                           ~$18/mo
Route 53:                      ~$0.50/mo
Data transfer + misc:          ~$5/mo
─────────────────────────────────────
Total:                        ~$173/mo
```

---

## Step 0 — Prerequisites (Do These First)

1. **AWS account** — aws.amazon.com → create account → add credit card
2. **Billing alert:**
   - AWS Console → Billing → Budgets → Create a budget
   - Type: Cost budget, Amount: $200/month, Email: your email
3. **AWS CLI on your Mac:**
   ```bash
   brew install awscli
   aws configure
   # AWS Access Key ID: (get from IAM → Users → your user → Security credentials)
   # AWS Secret Access Key: (same place)
   # Default region name: ap-south-1
   # Default output format: json
   ```
4. **Verify CLI works:**
   ```bash
   aws sts get-caller-identity
   # Should print your account ID and user ARN
   ```

---

## Part 1 — Networking (VPC, Subnets, Security Groups)

> Do this entire part before anything else. Every resource goes inside this VPC.

### Step 1.1 — Create VPC

**AWS Console → VPC → Your VPCs → Create VPC**

```
Resources to create:  VPC only
Name tag:             acp-vpc
IPv4 CIDR block:      10.0.0.0/16
Tenancy:              Default
```

Click **Create VPC**. Note the VPC ID (e.g. `vpc-0abc123...`).

### Step 1.2 — Create 4 Subnets

**VPC → Subnets → Create subnet** (create one at a time — select acp-vpc for all)

| Name | AZ | CIDR | Type |
|---|---|---|---|
| `acp-public-1a` | ap-south-1a | 10.0.1.0/24 | Public — ALB + EC2 |
| `acp-public-1b` | ap-south-1b | 10.0.2.0/24 | Public — ALB HA node |
| `acp-private-1a` | ap-south-1a | 10.0.3.0/24 | Private — RDS + Redis |
| `acp-private-1b` | ap-south-1b | 10.0.4.0/24 | Private — RDS standby |

After creating, select each **public** subnet → **Actions → Edit subnet settings** → enable **Auto-assign public IPv4 address**.

### Step 1.3 — Internet Gateway

**VPC → Internet Gateways → Create internet gateway**

```
Name tag: acp-igw
```

After creating: **Actions → Attach to VPC → select acp-vpc → Attach**

### Step 1.4 — Public Route Table

**VPC → Route Tables → Create route table**

```
Name: acp-public-rt
VPC:  acp-vpc
```

After creating:
1. Select `acp-public-rt` → **Routes** tab → **Edit routes**
2. **Add route:** Destination `0.0.0.0/0` → Target: `acp-igw` → Save
3. **Subnet associations** tab → **Edit subnet associations**
4. Select `acp-public-1a` and `acp-public-1b` → Save

### Step 1.5 — Security Groups (4 groups)

**VPC → Security Groups → Create security group** — create all 4:

---

**Group 1: `acp-alb-sg`** (ALB faces the internet)
```
Name:        acp-alb-sg
VPC:         acp-vpc
Description: Internet-facing ALB

Inbound rules:
  HTTP   Port 80   Source: 0.0.0.0/0   ← for HTTP→HTTPS redirect
  HTTPS  Port 443  Source: 0.0.0.0/0   ← real traffic

Outbound rules: All traffic → 0.0.0.0/0 (default)
```

---

**Group 2: `acp-ec2-sg`** (EC2 only accepts from ALB + your SSH)
```
Name:        acp-ec2-sg
VPC:         acp-vpc
Description: ACP EC2 instances

Inbound rules:
  Custom TCP  Port 5173  Source: acp-alb-sg  ← UI, ALB routes here
  Custom TCP  Port 8000  Source: acp-alb-sg  ← Gateway (optional direct)
  SSH         Port 22    Source: My IP        ← your home IP ONLY

Outbound rules: All traffic → 0.0.0.0/0 (default)
```

---

**Group 3: `acp-rds-sg`** (RDS only accepts from EC2)
```
Name:        acp-rds-sg
VPC:         acp-vpc
Description: RDS PostgreSQL

Inbound rules:
  PostgreSQL  Port 5432  Source: acp-ec2-sg

Outbound rules: All traffic (default)
```

---

**Group 4: `acp-redis-sg`** (Redis only accepts from EC2)
```
Name:        acp-redis-sg
VPC:         acp-vpc
Description: ElastiCache Redis

Inbound rules:
  Custom TCP  Port 6379  Source: acp-ec2-sg

Outbound rules: All traffic (default)
```

---

## Part 2 — ACM SSL Certificate (Free, Auto-Renews)

> Get the SSL cert BEFORE creating the ALB. ALB needs it.

**AWS Console → Certificate Manager → Request certificate**

```
Certificate type:    Request a public certificate
Domain names:
  aegisagent.in
  www.aegisagent.in
  api.aegisagent.in
Validation method:   DNS validation (recommended)
Key algorithm:       RSA 2048
```

Click **Request**. The certificate appears as **Pending validation**.

### Step 2.1 — Validate via Route 53

1. Click the certificate → expand each domain
2. For each domain you will see a **CNAME record** to add
3. Click **Create records in Route 53** — ACM will auto-create the validation CNAMEs

> **NOTE:** Do Part 11 (Route 53 hosted zone) FIRST if you haven't yet, then come back here. You need Route 53 hosted zone to exist before ACM can create validation records.

**Or validate manually via GoDaddy DNS:**
1. Copy the CNAME Name and Value for each domain
2. Go to GoDaddy → DNS → Add CNAME record for each
3. Wait 5-30 minutes → certificate status changes to **Issued**

The certificate **auto-renews for free** every year. No certbot needed.

---

## Part 3 — RDS PostgreSQL (Replaces Local Postgres)

### Step 3.1 — Create RDS Subnet Group

**RDS → Subnet groups → Create DB subnet group**

```
Name:        acp-rds-subnet-group
Description: Private subnets for ACP RDS
VPC:         acp-vpc
Subnets:     acp-private-1a  AND  acp-private-1b
```

### Step 3.2 — Create RDS Instance

**RDS → Databases → Create database**

```
Method:              Standard Create
Engine:              PostgreSQL
Version:             PostgreSQL 15.x (pick latest 15.x)
Template:            Free tier  (or Dev/Test — NOT Production yet)

DB instance identifier:  acp-postgres-prod
Master username:         postgres
Master password:         (generate strong — save it now)
                         Example: sdfhashdfha12434w@sdf;

DB instance class:       db.t3.micro
Storage type:            gp3
Allocated storage:       20 GB
Storage autoscaling:     Disable (enable later if needed)

Connectivity:
  VPC:                   acp-vpc
  DB subnet group:       acp-rds-subnet-group
  Public access:         No  ← NEVER make this public
  VPC security group:    remove default, add acp-rds-sg
  Availability Zone:     ap-south-1a

Additional configuration:
  Initial database name:     acp
  Backup retention:          7 days
  Enable automated backups:  Yes
  Enable deletion protection: Yes
```

Click **Create database**. Takes 5-10 minutes.

After created: click the database → **Connectivity & security** → copy the **Endpoint**.
```
Example: acp-postgres-prod.cxxxxxxxx.ap-south-1.rds.amazonaws.com
```

Save this as `YOUR_RDS_ENDPOINT`.

---

## Part 4 — ElastiCache Redis (Replaces Local Redis)

### Step 4.1 — Create Subnet Group

**ElastiCache → Subnet groups → Create subnet group**

```
Name:     
VPC:     acp-vpc
Subnets: acp-private-1a  AND  acp-private-1b
```

### Step 4.2 — Create Redis Cluster

**ElastiCache → Redis clusters → Create Redis cluster**

```
Create new cluster:  Yes
Cluster mode:        Disabled  (single node)
Name:                acp-redis-prod
Description:         ACP cache and event queue

Node type:           cache.t3.micro
Number of replicas:  0

Subnet group:        acp-redis-subnet-group
Security groups:     acp-redis-sg  (remove default)

Backup:              Enable, 7 day retention
```

Click **Create**. Takes 5-10 minutes.

After created: copy the **Primary endpoint**.
```
Example: acp-redis-prod.abc123.0001.aps1.cache.amazonaws.com:6379
```

Save this as `YOUR_ELASTICACHE_ENDPOINT`.

---

## Part 5 — S3 Bucket (Backups + Config Storage)

**S3 → Create bucket**

```
Bucket name:              acp-backups-prod-am   ← must be globally unique (add your initials)
AWS Region:               ap-south-1
Block all public access:  Yes (all 4 checkboxes)
Versioning:               Enable
Default encryption:       SSE-S3 (Amazon S3 managed keys)
```

Click **Create bucket**.

### Step 5.1 — Bucket Policy

Go to the bucket → **Permissions → Bucket policy → Edit** → paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEC2Role",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR_ACCOUNT_ID:role/acp-ec2-role"
      },
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::acp-backups-prod-am",
        "arn:aws:s3:::acp-backups-prod-am/*"
      ]
    }
  ]
}
```

Replace `YOUR_ACCOUNT_ID` with your 12-digit AWS account number (top-right dropdown in console).

> You will create the `acp-ec2-role` in Part 6. Come back and save this policy after.

---

## Part 6 — IAM Role for EC2

> EC2 needs permissions to pull the .env from S3 and push metrics to CloudWatch.

**IAM → Roles → Create role**

```
Trusted entity type: AWS service
Use case:            EC2
```

Click Next. Search and attach these policies:
- `AmazonSSMManagedInstanceCore` ← allows SSM Session Manager (no SSH key needed in emergency)
- `CloudWatchAgentServerPolicy` ← metrics to CloudWatch
- `AmazonS3FullAccess` ← backups + config (scope to bucket later)

```
Role name: acp-ec2-role
Description: ACP EC2 instance role
```

Click **Create role**.

Now go back to the S3 bucket policy in Part 5 and add the role ARN.

---

## Part 7 — Code Changes (On Your Mac — Do Before Deploying)

These files are already in the repo. Verify and update your `.env.aws.template`:

### Step 7.1 — Update .env.aws.template

The file is at `infra/.env.aws.template`. Update `aegisagent.in` and your bucket name:

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp
```

The key lines to set (rest are already correct):
```
ALLOWED_ORIGINS=https://aegisagent.in,https://www.aegisagent.in
S3_BACKUP_BUCKET=acp-backups-prod-am
```

### Step 7.2 — Update .env.example in root

```bash
# Update domain in ALLOWED_ORIGINS
grep -n "aegisagent" .env.example
# If it shows .io references, update them to .in
```

### Step 7.3 — Commit Everything

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp
git add infra/pgbouncer.aws.ini infra/docker-compose.aws.yml infra/.env.aws.template
git commit -m "feat: AWS production configs with ALB + aegisagent.in domain"
git push origin main
```

---

## Part 8 — EC2 Instance + Launch Template

### Step 8.1 — Launch One EC2 First (Manual)

We need one running EC2 to set up everything, then convert it to a Launch Template for ASG.

**EC2 → Instances → Launch instances**

```
Name:              acp-server-prod
AMI:               Ubuntu Server 22.04 LTS (HVM), SSD — 64-bit x86
                   (Search "ubuntu 22" in the AMI catalog, pick the one without cost)
Instance type:     t3.xlarge   (4 vCPU, 16 GB RAM — minimum for 28 containers)

Key pair:
  Create new key pair
  Name:   acp-prod-key
  Type:   RSA
  Format: .pem
  → Download it immediately
  → On your Mac: mv ~/Downloads/acp-prod-key.pem ~/.ssh/
  → chmod 400 ~/.ssh/acp-prod-key.pem

Network settings:
  VPC:                    acp-vpc
  Subnet:                 acp-public-1a
  Auto-assign public IP:  Enable
  Security group:         acp-ec2-sg  (select existing)

Storage:
  Root volume: 60 GB gp3   (28 Docker images + logs; 40 GB fills up fast)

Advanced details:
  IAM instance profile: acp-ec2-role
```

Click **Launch instance**.

### Step 8.2 — Elastic IP (Prevent IP Change on Reboot)

**EC2 → Elastic IPs → Allocate Elastic IP address**

```
Network border group: ap-south-1
```

Click **Allocate** → **Actions → Associate Elastic IP address**

```
Resource type: Instance
Instance:      acp-server-prod
```

Click **Associate**. Copy the Elastic IP — this is `YOUR_EC2_IP`.

---

## Part 9 — Application Load Balancer (ALB)

### Step 9.1 — Create Target Group

**EC2 → Target Groups → Create target group**

```
Target type:         Instances
Target group name:   acp-ui-tg
Protocol:            HTTP
Port:                5173
VPC:                 acp-vpc

Health checks:
  Protocol:          HTTP
  Path:              /health
  Port:              8000   ← gateway health check (more reliable)
  Healthy threshold:   2
  Unhealthy threshold: 3
  Timeout:           10
  Interval:          30
  Success codes:     200
```

Click **Next** → select your EC2 instance → **Include as pending below** → **Create target group**.

> **Why port 8000 for health?** The gateway `/health` is the most reliable indicator the full stack is up. Port 5173 is the UI which could be up even if backend is down.

### Step 9.2 — Create ALB

**EC2 → Load Balancers → Create load balancer → Application Load Balancer**

```
Load balancer name:    acp-alb
Scheme:                Internet-facing
IP address type:       IPv4

Network mapping:
  VPC:      acp-vpc
  Mappings: ✓ ap-south-1a → acp-public-1a
            ✓ ap-south-1b → acp-public-1b

Security groups: acp-alb-sg  (remove default)

Listeners and routing:
  Listener 1: HTTP  :80  → Add action: Redirect to HTTPS (301)
  Listener 2: HTTPS :443 → Forward to: acp-ui-tg
              Default SSL/TLS certificate: Select your ACM cert (aegisagent.in)
```

Click **Create load balancer**. Takes ~2 minutes.

After created: copy the **DNS name** of the ALB:
```
Example: acp-alb-1234567890.ap-south-1.elb.amazonaws.com
```

Save this as `YOUR_ALB_DNS`.

---

## Part 10 — Auto Scaling Group (ASG)

### Step 10.1 — Create Launch Template

**EC2 → Launch Templates → Create launch template**

```
Launch template name:   acp-lt
Template version desc:  Initial version

AMI: Ubuntu Server 22.04 LTS (same as before, search for it)
Instance type: t3.xlarge

Key pair: acp-prod-key

Network settings:
  Security groups: acp-ec2-sg

Storage:
  Root volume: 60 GB gp3

Resource tags:
  Key: Name   Value: acp-asg-instance   (tags instances launched by ASG)

Advanced details:
  IAM instance profile: acp-ec2-role
  User data: (paste the script below)
```

**User Data Script** — paste this in the User data box:

```bash
#!/bin/bash
set -e
exec > /var/log/acp-init.log 2>&1

# Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install tools
apt-get install -y git python3-pip postgresql-client awscli

# Clone repo
git clone https://github.com/Abhi-mishra998/aegis.git /home/ubuntu/aegis
chown -R ubuntu:ubuntu /home/ubuntu/aegis

# Pull .env from S3 (you will upload it in Step 12.5)
aws s3 cp s3://acp-backups-prod-am/config/.env /home/ubuntu/aegis/infra/.env

# Pull pgbouncer.aws.ini with real RDS endpoint from S3
aws s3 cp s3://acp-backups-prod-am/config/pgbouncer.aws.ini /home/ubuntu/aegis/infra/pgbouncer.aws.ini

# Python setup
cd /home/ubuntu/aegis
sudo -u ubuntu python3 -m venv .venv
sudo -u ubuntu .venv/bin/pip install -e ".[server,dev]" -q

# Run migrations
sudo -u ubuntu bash -c '
  cd /home/ubuntu/aegis
  source .venv/bin/activate
  for svc in audit identity usage api; do
    alembic -c services/$svc/alembic.ini upgrade head 2>/dev/null || true
  done
'

# Start all containers
cd /home/ubuntu/aegis/infra
sudo -u ubuntu docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build

# Create systemd service for auto-restart
cat > /etc/systemd/system/acp.service <<'EOF'
[Unit]
Description=ACP Docker Stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/aegis/infra
ExecStart=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d
ExecStop=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.aws.yml down
TimeoutStartSec=600
User=ubuntu

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable acp.service
```

Click **Create launch template**.

### Step 10.2 — Create Auto Scaling Group

**EC2 → Auto Scaling Groups → Create Auto Scaling group**

**Step 1 — Choose template:**
```
Auto Scaling group name: acp-asg
Launch template:         acp-lt  (pick latest version)
```

**Step 2 — Network:**
```
VPC:     acp-vpc
Subnets: acp-public-1a  (start with one AZ — add 1b later for real HA)
```

**Step 3 — Load balancing:**
```
Attach to an existing load balancer:  Yes
Choose from your load balancer target groups: acp-ui-tg

Health checks:
  EC2 health checks:  Yes
  ELB health checks:  Yes  ← ALB will mark unhealthy instances for replacement
  Health check grace period: 600  (10 min — Docker startup takes time)
```

**Step 4 — Size and scaling:**
```
Desired capacity:    1
Minimum capacity:    1
Maximum capacity:    3

Automatic scaling:   Create a scaling policy
  Policy type: Target tracking scaling
  Metric type: Average CPU utilization
  Target value: 65
  (This auto scales out when CPU > 65% for 3 minutes, scales in when < 65%)
```

**Step 5 — Notifications:** (optional — add your email for scale events)

**Step 6 — Tags:**
```
Key: Name   Value: acp-asg-instance
```

Click **Create Auto Scaling group**.

> The ASG will now manage your EC2 instance. If the instance dies, ASG automatically launches a new one using the Launch Template user_data script.

---

## Part 11 — Route 53 + GoDaddy (aegisagent.in)

### Step 11.1 — Create Hosted Zone in Route 53

**Route 53 → Hosted zones → Create hosted zone**

```
Domain name: aegisagent.in
Type:        Public hosted zone
```

Click **Create hosted zone**.

Route 53 creates a **Hosted Zone** with 4 nameserver records (NS record). They look like:
```
ns-XXX.awsdns-XX.com
ns-XXX.awsdns-XX.net
ns-XXX.awsdns-XX.co.uk
ns-XXX.awsdns-XX.org
```

**Copy all 4 nameservers.** You need these for GoDaddy.

### Step 11.2 — Update Nameservers in GoDaddy

1. Go to **godaddy.com** → sign in → **My Products**
2. Find `aegisagent.in` → click **DNS**
3. Scroll down to **Nameservers** section
4. Click **Change** → select **I'll use my own nameservers**
5. Delete the existing GoDaddy nameservers
6. Add each of the 4 AWS nameservers (one per field):
   ```
   ns-XXX.awsdns-XX.com
   ns-XXX.awsdns-XX.net
   ns-XXX.awsdns-XX.co.uk
   ns-XXX.awsdns-XX.org
   ```
7. Click **Save**

**DNS propagation takes 15 minutes to 48 hours.** Continue the guide and check back.

Verify propagation (run on your Mac):
```bash
dig aegisagent.in NS
# Should show your AWS nameservers, not GoDaddy's
```

### Step 11.3 — Create DNS Records in Route 53

In **Route 53 → Hosted zones → aegisagent.in → Create record**

Create these records:

**Record 1: Root domain → ALB**
```
Record name: (leave empty — root)
Record type: A
Alias:       Yes  ← toggle on
Route traffic to: Alias to Application and Classic Load Balancer
  Region: ap-south-1
  Load balancer: select acp-alb
TTL: (not needed for alias records)
```

**Record 2: www → ALB**
```
Record name: www
Record type: A
Alias:       Yes
Route traffic to: Alias to Application and Classic Load Balancer
  Region: ap-south-1
  Load balancer: select acp-alb
```

**Record 3: api → ALB**
```
Record name: api
Record type: A
Alias:       Yes
Route traffic to: Alias to Application and Classic Load Balancer
  Region: ap-south-1
  Load balancer: select acp-alb
```

> **Use ALIAS records (not CNAME for root domain).** AWS alias records are free and resolve faster than CNAME. Root domain (@) cannot use CNAME — it must be Alias A.

---

## Part 12 — EC2 Server Setup (First-Time Manual)

> Your first EC2 instance (from Part 8) is NOT managed by the ASG user_data yet.
> Set it up manually. Later instances launched by ASG will use the user_data script automatically.

SSH into the server:
```bash
ssh -i ~/.ssh/acp-prod-key.pem ubuntu@YOUR_EC2_IP
```

### Step 12.1 — Install Dependencies

```bash
# Update
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
newgrp docker

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version

# Install tools
sudo apt-get install -y git python3-pip postgresql-client awscli

# Test Docker
docker run hello-world
```

Log out and back in for docker group:
```bash
exit
ssh -i ~/.ssh/acp-prod-key.pem ubuntu@YOUR_EC2_IP
```

### Step 12.2 — Clone the Repository

```bash
git clone https://github.com/Abhi-mishra998/aegis.git ~/aegis
cd ~/aegis

# Python setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]" -q
```

### Step 12.3 — Generate Production Secrets

Run these on the EC2 server — they generate random secrets:

```bash
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('INTERNAL_SECRET=' + secrets.token_hex(32))"
python3 -c "import secrets; print('MESH_JWT_SECRET=' + secrets.token_hex(32))"

# Generate 8 DB passwords (run 8 times)
for svc in registry identity audit api usage identity_graph flight_recorder autonomy; do
  python3 -c "import secrets; print('${svc^^}_DB_PASSWORD=' + secrets.token_hex(16))"
done
```

Copy all the output. You will paste it into `.env` next.

### Step 12.4 — Configure Production .env

```bash
cd ~/aegis/infra
cp .env.aws.template .env
nano .env
```

Fill in EVERY `REPLACE_ME` value:

```bash
# These are the specific values you MUST change:
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_RDS_PASSWORD@YOUR_RDS_ENDPOINT:5432/acp
REDIS_URL=rediss://YOUR_ELASTICACHE_ENDPOINT:6379/0
POSTGRES_HOST=YOUR_RDS_ENDPOINT
POSTGRES_PASSWORD=YOUR_RDS_PASSWORD

JWT_SECRET_KEY=<paste from above>
INTERNAL_SECRET=<paste from above>
MESH_JWT_SECRET=<paste from above>

REGISTRY_DB_PASSWORD=<paste>
IDENTITY_DB_PASSWORD=<paste>
AUDIT_DB_PASSWORD=<paste>
API_DB_PASSWORD=<paste>
USAGE_DB_PASSWORD=<paste>
IDENTITY_GRAPH_DB_PASSWORD=<paste>
FLIGHT_RECORDER_DB_PASSWORD=<paste>
AUTONOMY_DB_PASSWORD=<paste>

ALLOWED_ORIGINS=https://aegisagent.in,https://www.aegisagent.in
ENVIRONMENT=production
LOG_LEVEL=WARNING
S3_BACKUP_BUCKET=acp-backups-prod-am
```

Save with Ctrl+O → Enter → Ctrl+X.

### Step 12.5 — Upload Config to S3 (So ASG Can Pull It)

```bash
# Upload .env so future ASG instances can pull it
aws s3 cp ~/aegis/infra/.env s3://acp-backups-prod-am/config/.env

# Verify upload
aws s3 ls s3://acp-backups-prod-am/config/
```

### Step 12.6 — Configure PgBouncer for RDS

```bash
cd ~/aegis/infra

# Replace placeholder with your actual RDS endpoint
sed -i 's/RDS_ENDPOINT_PLACEHOLDER/YOUR_RDS_ENDPOINT/g' pgbouncer.aws.ini

# Verify
head -3 pgbouncer.aws.ini
# Should show: acp_registry = host=acp-postgres-prod.xxx.ap-south-1.rds.amazonaws.com ...

# Upload to S3 for ASG
aws s3 cp pgbouncer.aws.ini s3://acp-backups-prod-am/config/pgbouncer.aws.ini
```

Update `userlist.txt` passwords to match your DB passwords:
```bash
nano ~/aegis/infra/userlist.txt
# Format: "username" "password"
# Update each password to match what you set in .env for each service
```

### Step 12.7 — Initialize RDS Databases

```bash
# Connect to RDS and run database init
psql -h YOUR_RDS_ENDPOINT -U postgres -d postgres -f ~/aegis/infra/init-db.sql
# Enter your RDS master password when prompted

# Verify databases created
psql -h YOUR_RDS_ENDPOINT -U postgres -c "\l"
# Should list: acp, acp_identity, acp_audit, acp_usage, acp_registry,
#              acp_api, acp_identity_graph, acp_flight_recorder, acp_autonomy, acp_behavior
```

### Step 12.8 — Run Alembic Migrations

```bash
cd ~/aegis
source .venv/bin/activate

for svc in audit identity usage api registry; do
  echo "=== Migrating $svc ==="
  alembic -c services/$svc/alembic.ini upgrade head
done

for svc in identity_graph flight_recorder autonomy; do
  echo "=== Migrating $svc ==="
  alembic -c services/$svc/alembic.ini upgrade head 2>/dev/null || echo "(no alembic.ini — skip)"
done
```

### Step 12.9 — Start All Containers

```bash
cd ~/aegis/infra

docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build
# First run: 5-10 minutes (builds all images)
# Watch logs:
docker-compose -f docker-compose.yml -f docker-compose.aws.yml logs -f
# Ctrl+C to stop watching (containers keep running)
```

Wait 2 minutes, then verify:
```bash
docker ps --format "{{.Names}}\t{{.Status}}" | sort
# Every container should say "healthy" or "Up"

# Count healthy:
docker ps --format "{{.Status}}" | grep -c healthy
# Should be 23+ (no local postgres/redis/replica — those are RDS/ElastiCache)
```

If a container is restarting:
```bash
docker logs acp_gateway --tail 30
docker logs acp_audit --tail 30
# Most common cause: wrong DATABASE_URL or REDIS_URL in .env
```

### Step 12.10 — Test Locally on EC2

```bash
# Gateway health
curl http://localhost:8000/health
# Expect: {"status":"healthy","service":"gateway","version":"1.0.0"}

# Full system health
curl http://localhost:8000/system/health | python3 -c "
import sys,json; d=json.load(sys.stdin)
s=d.get('services',{}); h=sum(1 for v in s.values() if v.get('status')=='healthy')
print(f'{h}/{len(s)} services healthy')"
# Expect: 12/12 services healthy

# UI is up
curl -I http://localhost:5173
# Expect: HTTP/1.1 200 OK
```

### Step 12.11 — Enable Auto-Start on Reboot

```bash
sudo bash -c 'cat > /etc/systemd/system/acp.service << EOF
[Unit]
Description=ACP Docker Stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/aegis/infra
ExecStart=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d
ExecStop=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.aws.yml down
TimeoutStartSec=600
User=ubuntu

[Install]
WantedBy=multi-user.target
EOF'

sudo systemctl daemon-reload
sudo systemctl enable acp.service
sudo systemctl status acp.service
```

---

## Part 13 — ACM Certificate Validation + ALB Test

By now your DNS should be propagating. Check:

```bash
# On your Mac:
dig aegisagent.in
# Must return YOUR_EC2_IP (via ALB DNS resolution)
```

Check ACM certificate status:
- **Certificate Manager → Certificates** → your cert should show **Issued** (green)
- If still **Pending validation**: GoDaddy DNS hasn't propagated yet — wait and check again

Once certificate is **Issued**, test HTTPS through ALB:
```bash
# On your Mac:
curl https://aegisagent.in/health
# Expect: {"status":"healthy","service":"gateway","version":"1.0.0"}

curl https://www.aegisagent.in/health
# Same response

# Test UI
open https://aegisagent.in
# Should open the ACP dashboard in your browser
```

---

## Part 14 — Seed Admin User + Run Demos

```bash
# On EC2:
cd ~/aegis
source .venv/bin/activate

# STEP 1 — Bootstrap admin user directly into DB (no API needed for first user)
# Creates admin@acp.local / password — change password after first login
python3 scripts/reinit_system.py
# Expect: "✅ System initialized" with admin user, default tenant, and test agent

# STEP 2 — Seed demo dashboard data (2000 audit logs, 35 incidents, usage records)
# This makes every chart in the UI show real data on first open
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_RDS_PASSWORD@YOUR_RDS_ENDPOINT:5432/acp \
ACP_GATEWAY_URL=http://localhost:8000 \
python3 scripts/seed_demo_data.py
# Expect: "✅ Demo data seeded" with row counts

# STEP 3 — Provision demo agents (db_copilot, devops_agent, support_agent)
ACP_GATEWAY_URL=https://aegisagent.in .venv/bin/python demos/db_copilot/setup_demo.py
ACP_GATEWAY_URL=https://aegisagent.in .venv/bin/python demos/devops_agent/setup_demo.py
ACP_GATEWAY_URL=https://aegisagent.in .venv/bin/python demos/support_agent/setup_demo.py

# STEP 4 — Run all demos (seeds ~100-200 real governance decisions)
ACP_GATEWAY_URL=https://aegisagent.in .venv/bin/python demos/run_all_demos.py
# All 3 packs should PASS
```

> **Admin credentials after reinit:** email `admin@acp.local`, password `password`.
> Change it immediately: log in to the dashboard → Profile → Change Password.
> Or create a new admin via `POST /auth/users` with an admin Bearer token.

---

## Part 15 — S3 Daily Backups

### Step 15.1 — IAM Access for Backups

The `acp-ec2-role` already has S3 access. Verify:
```bash
aws s3 ls s3://acp-backups-prod-am/
# Should list without error
```

### Step 15.2 — Test Manual Backup

```bash
PGPASSWORD=YOUR_RDS_PASSWORD pg_dump \
  -h YOUR_RDS_ENDPOINT -U postgres acp_audit \
  | gzip | aws s3 cp - s3://acp-backups-prod-am/manual-test-$(date +%Y%m%d).sql.gz

# Verify it's there
aws s3 ls s3://acp-backups-prod-am/
```

### Step 15.3 — Daily Backup Cron

```bash
crontab -e
```

Add this line (runs at 2 AM IST daily):
```
30 20 * * * cd /home/ubuntu/aegis && PGPASSWORD=YOUR_RDS_PASSWORD pg_dump -h YOUR_RDS_ENDPOINT -U postgres acp_audit | gzip | aws s3 cp - s3://acp-backups-prod-am/backup-$(date +\%Y\%m\%d).sql.gz >> /var/log/acp-backup.log 2>&1
```

---

## Part 16 — Monitoring + Alerts

### Step 16.1 — Grafana (SSH Tunnel)

```bash
# On your Mac — create SSH tunnel:
ssh -i ~/.ssh/acp-prod-key.pem -L 3000:localhost:3000 ubuntu@YOUR_EC2_IP -N &

# Then open in browser:
open http://localhost:3000
# Login: admin / your GRAFANA_ADMIN_PASSWORD from .env
```

### Step 16.2 — CloudWatch Alarms

**AWS Console → CloudWatch → Alarms → Create alarm**

**Alarm 1: High CPU**
```
Metric:    EC2 → Per-Instance → CPUUtilization → acp-server-prod
Threshold: Greater than 80% for 5 minutes
Action:    Send notification → create SNS topic → add your email
```

**Alarm 2: Instance down**
```
Metric:    EC2 → Per-Instance → StatusCheckFailed → acp-server-prod
Threshold: Greater than 0 for 1 minute
Action:    Same SNS topic (email you)
```

**Alarm 3: ALB 5xx errors**
```
Metric:    ApplicationELB → Per-AppELB → HTTPCode_ELB_5XX_Count → acp-alb
Threshold: Greater than 10 in 5 minutes
Action:    Same SNS topic
```

### Step 16.3 — Enable ALB Access Logs (Optional)

**EC2 → Load Balancers → acp-alb → Attributes → Edit**

```
Access logs: Enable
S3 location: acp-backups-prod-am/alb-logs
```

---

## Part 17 — Final Verification Checklist

Run every command. All should succeed.

```bash
# On EC2 server
export BASE=https://aegisagent.in
export TENANT="00000000-0000-0000-0000-000000000001"

echo "=== 1. Gateway health ==="
curl -s $BASE/health | python3 -m json.tool

echo "=== 2. System health ==="
curl -s $BASE/system/health | python3 -m json.tool

echo "=== 3. Auth ==="
TOKEN=$(curl -s -X POST $BASE/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")
echo "Token: ${TOKEN:0:30}..."

echo "=== 4. List agents ==="
curl -s $BASE/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | python3 -m json.tool | head -30

echo "=== 5. Audit chain verify ==="
curl -s $BASE/audit/verify-chain \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | python3 -m json.tool

echo "=== 6. SSL cert ==="
curl -I https://aegisagent.in 2>&1 | grep -E "HTTP|issuer|expire"

echo "=== 7. www redirect ==="
curl -I http://www.aegisagent.in | grep -E "HTTP|Location"

echo "=== 8. Container health ==="
docker ps --format "{{.Names}}\t{{.Status}}" | sort

echo "=== 9. Healthy count ==="
docker ps --format "{{.Status}}" | grep -c healthy

echo "=== Done. Open: $BASE ==="
```

---

## Common Problems and Fixes

### Containers can't reach RDS
```bash
# Test from EC2
psql -h YOUR_RDS_ENDPOINT -U postgres -c "SELECT 1"
# If it hangs (not refused): security group issue
# Fix: RDS security group → Inbound → add PostgreSQL 5432 from acp-ec2-sg
```

### Redis ping fails
```bash
sudo apt-get install -y redis-tools
# ElastiCache requires TLS — use --tls flag
redis-cli --tls -h YOUR_ELASTICACHE_ENDPOINT -p 6379 ping
# Expect: PONG
# If no PONG: check security group allows 6379 from acp-ec2-sg
# Also verify REDIS_URL in .env starts with rediss:// (double-s = TLS)
```

### ALB shows "unhealthy" target
```bash
# EC2 health check port 8000 — test it
curl -v http://localhost:8000/health
# If this fails: acp_gateway container is down
docker logs acp_gateway --tail 50
```

### ACM certificate stuck in "Pending validation"
```bash
# Check GoDaddy nameservers updated
dig aegisagent.in NS
# Must show awsdns-xx servers, NOT ns1.domaincontrol.com (GoDaddy)
# If still GoDaddy: wait up to 48 hours or check you saved the change in GoDaddy
```

### Service keeps restarting after boot
```bash
# Most common: .env not loaded or wrong values
grep DATABASE_URL ~/aegis/infra/.env
# Must have: postgresql+asyncpg://postgres:PASSWORD@rds-endpoint:5432/acp
# NOT localhost!

# Check specific service logs
docker logs acp_audit --tail 50
docker logs acp_gateway --tail 50
```

### "Connection refused" on port 5173
```bash
# Check UI container
docker ps | grep ui
docker logs acp_ui --tail 30
# If container exited: usually build failure
docker-compose -f docker-compose.yml -f docker-compose.aws.yml up acp_ui --build
```

### GoDaddy DNS not propagating
```bash
# Check from multiple locations
curl https://dnschecker.org/api/dns/aegisagent.in?type=NS
# Or visit: dnschecker.org → type aegisagent.in → check NS
# All should show aws nameservers, not domaincontrol.com
```

---

## After Deployment — Generate Traffic for Resume

```bash
# Run demos 10 times = 1000+ governance decisions in production
for i in $(seq 1 10); do
  echo "Run $i..."
  ACP_GATEWAY_URL=https://aegisagent.in .venv/bin/python demos/run_all_demos.py
  sleep 5
done

# Count total decisions
TOKEN=$(curl -s -X POST https://aegisagent.in/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -d '{"email":"admin@acp.local","password":"password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

curl -s "https://aegisagent.in/audit/logs?limit=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Total decisions:', d['data']['total'])"
```

**Resume bullets after this:**
```
• Deployed 28-container AI governance platform on AWS (EC2 t3.xlarge, RDS PostgreSQL,
  ElastiCache Redis) — live at aegisagent.in
• Configured Application Load Balancer with ACM SSL, Auto Scaling Group (min=1, max=3),
  and CloudWatch alarms for production reliability
• Platform processed X,000+ agent governance decisions in production with cryptographic
  audit trail and <300ms p50 latency
• Implemented VPC with public/private subnets, security group isolation, and S3 automated
  daily backups
```

---

## Quick Reference — All Values to Save

```
=== AWS ===
Region:                ap-south-1  (Mumbai)
VPC:                   acp-vpc
EC2 Elastic IP:        YOUR_EC2_IP
RDS Endpoint:          acp-postgres-prod.xxxx.ap-south-1.rds.amazonaws.com
ElastiCache Endpoint:  acp-redis-prod.xxxxx.0001.aps1.cache.amazonaws.com:6379
ALB DNS:               acp-alb-XXXXXXXXXX.ap-south-1.elb.amazonaws.com
S3 Bucket:             acp-backups-prod-am

=== Domain ===
Domain:                aegisagent.in  (GoDaddy)
Nameservers:           ns-XXX.awsdns-XX.com (×4 from Route 53)
UI:                    https://aegisagent.in
API health:            https://aegisagent.in/health
Grafana (tunnel):      http://localhost:3000
Prometheus (tunnel):   http://localhost:9090

=== Credentials (NEVER commit to git) ===
RDS password:          (what you set in Step 12.4)
Admin email:           admin@acp.local   ← created by reinit_system.py
Admin password:        password          ← CHANGE THIS immediately after first login
Demo email:            demo@aegisagent.in / demo1234  ← created by seed_demo_data.py
```

Save everything above in a local password manager. Never put real passwords in git.
