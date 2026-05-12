# Dev environment - pdi-platform-dev (account 717423812395)

aws_profile = "pdi-platform-dev"
aws_region  = "us-east-1"
environment = "dev"

# Networking - platform_dev_us-east-1_vpc
vpc_id = "vpc-03d8d8f4fb1f915c5"

private_subnet_ids = [
  "subnet-0175f2446e10155c8", # platform-dev-private-services-01-us-east-1a
  "subnet-09fa7974f71f07c2a", # platform-dev-private-services-01-us-east-1b
]

public_subnet_ids = [
  "subnet-08196139ac2b08687", # platform-dev-public-ext01-us-east-1a
  "subnet-00cd06ad109a88780", # platform-dev-public-ext01-us-east-1b
]

# EKS
eks_cluster_version = "1.32"
node_instance_type  = "t3.medium"
node_min_size       = 1
node_max_size       = 2
node_desired_size   = 1

# DNS & TLS
route53_zone_id     = "Z09344573OY2RCX1Q76DP"
route53_zone_name   = "dev.platform.pditechnologies.com"
acm_certificate_arn = "arn:aws:acm:us-east-1:717423812395:certificate/c99bb8a8-6506-487e-a628-5a084c9ef69c"
hostname            = "holmesgpt.dev.platform.pditechnologies.com"

# LLM
anthropic_api_base = "https://ai-gateway.platform.pditechnologies.com"
anthropic_api_key  = "" # Set via TF_VAR_anthropic_api_key or -var flag
holmes_model       = "anthropic/claude-sonnet-4-6"

# Holmes
holmes_replicas  = 1
holmes_image_tag = "latest"

# UI Auth — Okta OIDC (PKCE)
okta_issuer              = "https://pdisoftware.okta.com/oauth2/default"
okta_client_id           = "0oa1ae04lowCIDE9B2p8"
holmes_super_admin_email = "srinivasreddy.v@pditechnologies.com"
okta_group_id             = "00g1ae0b43fuHGUXw2p8"
okta_api_token            = "" # Read from Secrets Manager via okta_api_token_secret_arn
okta_api_token_secret_arn = "arn:aws:secretsmanager:us-east-1:717423812395:secret:holmesgpt-dev/okta-api-token-32cDY1"

# MCP Integration API Keys
mcp_ado_api_key        = "" # Set via TF_VAR_mcp_ado_api_key or -var flag
mcp_atlassian_api_key  = "" # Set via TF_VAR_mcp_atlassian_api_key or -var flag
mcp_salesforce_api_key = "" # Set via TF_VAR_mcp_salesforce_api_key or -var flag

# Tags
tags = {
  Team        = "Tool COE"
  CostCenter  = "Cloud Engineering"
  Application = "holmesgpt"
}

# Logistics cross-account access
# HolmesReadOnly roles are deployed via infra/logistics-cross-account/ into each account.
# prod is intentionally excluded.
logistics_accounts = {
  logistics-ci = {
    account_id = "229743609213"
    role_arn   = "arn:aws:iam::229743609213:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-dev = {
    account_id = "690917928966"
    role_arn   = "arn:aws:iam::690917928966:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-stage = {
    account_id = "178396448338"
    role_arn   = "arn:aws:iam::178396448338:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-sandbox = {
    account_id = "087983023125"
    role_arn   = "arn:aws:iam::087983023125:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  logistics-prod = {
    account_id = "342706430250"
    role_arn   = "arn:aws:iam::342706430250:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-dev = {
    account_id = "689863073433"
    role_arn   = "arn:aws:iam::689863073433:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-prod = {
    account_id = "803964703583"
    role_arn   = "arn:aws:iam::803964703583:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-stage = {
    account_id = "415641701024"
    role_arn   = "arn:aws:iam::415641701024:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-legacy-prod = {
    account_id = "100161908138"
    role_arn   = "arn:aws:iam::100161908138:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-legacy-uat = {
    account_id = "294818304262"
    role_arn   = "arn:aws:iam::294818304262:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  pdi-pos-legacy-demo = {
    account_id = "226168396949"
    role_arn   = "arn:aws:iam::226168396949:role/HolmesReadOnly"
    region     = "eu-central-1"
  }
  gasbuddy = {
    account_id = "896521799855"
    role_arn   = "arn:aws:iam::896521799855:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  gasbuddy-staging = {
    account_id = "267230788984"
    role_arn   = "arn:aws:iam::267230788984:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  gasbuddy-marketing = {
    account_id = "773223718586"
    role_arn   = "arn:aws:iam::773223718586:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  gb-bp-client = {
    account_id = "607378507561"
    role_arn   = "arn:aws:iam::607378507561:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── C-Store Essentials ──────────────────────────────────────────────────
  ce-cstore-essentials-prod = {
    account_id = "386397235394"
    role_arn   = "arn:aws:iam::386397235394:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  ce-cstore-essentials-staging = {
    account_id = "179669678732"
    role_arn   = "arn:aws:iam::179669678732:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  ce-koupon-prod = {
    account_id = "185077157484"
    role_arn   = "arn:aws:iam::185077157484:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  ce-skupos-legacy-prod = {
    account_id = "025524405457"
    role_arn   = "arn:aws:iam::025524405457:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── CI POS ──────────────────────────────────────────────────────────────
  pdi-cipos-prod = {
    account_id = "271593336501"
    role_arn   = "arn:aws:iam::271593336501:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-cipos-stage = {
    account_id = "436020120639"
    role_arn   = "arn:aws:iam::436020120639:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Transpac / Comdata / Data Services ──────────────────────────────────
  pdi-transpac-prod = {
    account_id = "903333983563"
    role_arn   = "arn:aws:iam::903333983563:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-comdata-petroleader-prod = {
    account_id = "711387130277"
    role_arn   = "arn:aws:iam::711387130277:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-data-services-prod = {
    account_id = "090790636866"
    role_arn   = "arn:aws:iam::090790636866:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── ERP ─────────────────────────────────────────────────────────────────
  pdi-erp-prod = {
    account_id = "077614951579"
    role_arn   = "arn:aws:iam::077614951579:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-erp-stage = {
    account_id = "929611976443"
    role_arn   = "arn:aws:iam::929611976443:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── LMP Elevate ─────────────────────────────────────────────────────────
  pdi-lmp-elevate-prod = {
    account_id = "510376924091"
    role_arn   = "arn:aws:iam::510376924091:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-lmp-elevate-staging = {
    account_id = "008048648984"
    role_arn   = "arn:aws:iam::008048648984:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── MCS Payments ────────────────────────────────────────────────────────
  pdi-mcs-payments-prod = {
    account_id = "179616421945"
    role_arn   = "arn:aws:iam::179616421945:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-mcs-payments-staging = {
    account_id = "856536366562"
    role_arn   = "arn:aws:iam::856536366562:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Operations ──────────────────────────────────────────────────────────
  pdi-operations-prod = {
    account_id = "211125545481"
    role_arn   = "arn:aws:iam::211125545481:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-operations-stage = {
    account_id = "211125652818"
    role_arn   = "arn:aws:iam::211125652818:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Platform ────────────────────────────────────────────────────────────
  pdi-platform-prod = {
    account_id = "921714353219"
    role_arn   = "arn:aws:iam::921714353219:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-platform-stage = {
    account_id = "019652197448"
    role_arn   = "arn:aws:iam::019652197448:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── POS (additional stage account) ──────────────────────────────────────
  pdi-pos-stage-2 = {
    account_id = "974458387942"
    role_arn   = "arn:aws:iam::974458387942:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── RM Loyalty ──────────────────────────────────────────────────────────
  pdi-rm-loyalty-prod = {
    account_id = "651006557025"
    role_arn   = "arn:aws:iam::651006557025:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-rm-loyalty-staging = {
    account_id = "506628524632"
    role_arn   = "arn:aws:iam::506628524632:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-rm-loyalty-pci-prod = {
    account_id = "473106049869"
    role_arn   = "arn:aws:iam::473106049869:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-rm-loyalty-pci-staging = {
    account_id = "582802577213"
    role_arn   = "arn:aws:iam::582802577213:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── EP Payments ─────────────────────────────────────────────────────────
  aws-pdi-ep-payments-prod = {
    account_id = "198386896451"
    role_arn   = "arn:aws:iam::198386896451:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  aws-pdi-ep-payments-stage = {
    account_id = "003480668535"
    role_arn   = "arn:aws:iam::003480668535:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── ERP Skupos Retail Integrations ──────────────────────────────────────
  erp-skupos-retail-integrations-prod = {
    account_id = "640131422250"
    role_arn   = "arn:aws:iam::640131422250:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  erp-skupos-retail-integrations-stage = {
    account_id = "566348778577"
    role_arn   = "arn:aws:iam::566348778577:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Cybera ──────────────────────────────────────────────────────────────
  pdi-cybera-prod = {
    account_id = "483271369038"
    role_arn   = "arn:aws:iam::483271369038:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-cybera-stage = {
    account_id = "923510870796"
    role_arn   = "arn:aws:iam::923510870796:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Telapoint ───────────────────────────────────────────────────────────
  pdi-telapoint-prod = {
    account_id = "516716174645"
    role_arn   = "arn:aws:iam::516716174645:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Data Services (additional stage account) ────────────────────────────
  pdi-data-services-stage = {
    account_id = "445971787817"
    role_arn   = "arn:aws:iam::445971787817:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Fuel Pricing ────────────────────────────────────────────────────────
  pdi-fuelpricing-prod = {
    account_id = "498623468443"
    role_arn   = "arn:aws:iam::498623468443:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-fuelpricing-stage = {
    account_id = "118187397259"
    role_arn   = "arn:aws:iam::118187397259:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-fuelpricing-dev = {
    account_id = "758290227074"
    role_arn   = "arn:aws:iam::758290227074:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-fuelpricing-network = {
    account_id = "424429786528"
    role_arn   = "arn:aws:iam::424429786528:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-fuelpricing-shared = {
    account_id = "110678717330"
    role_arn   = "arn:aws:iam::110678717330:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  pdi-fuelpricing-test = {
    account_id = "875827703213"
    role_arn   = "arn:aws:iam::875827703213:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── Platform myPDI ──────────────────────────────────────────────────────
  pdi-platform-mypdi-prod = {
    account_id = "208790448711"
    role_arn   = "arn:aws:iam::208790448711:role/HolmesReadOnly"
    region     = "us-east-1"
  }
  # ── WebCAT ──────────────────────────────────────────────────────────────
  pdi-webcat-prod = {
    account_id = "374902171948"
    role_arn   = "arn:aws:iam::374902171948:role/HolmesReadOnly"
    region     = "us-east-1"
  }
}

# Enable the AWS MCP server addon now that real account IDs are set
aws_mcp_enabled = true



# ADO Webhook — set via TF_VAR_ environment variables, not here (secrets!)
# TF_VAR_ado_pat, TF_VAR_ado_organization, TF_VAR_ado_webhook_username, TF_VAR_ado_webhook_password
ado_organization       = "pdidev"
ado_webhook_username   = "holmesgpt"



# DBADash Web integration — credentials stored in Secrets Manager (fetched at runtime by the pod)
dbdash_api_url             = "https://db-monitor.shared.platform.pditechnologies.com"
dbdash_secrets_manager_arn = "arn:aws:secretsmanager:us-east-1:717423812395:secret:holmesgpt-dev/dbdash-web-credentials-g3D5at"