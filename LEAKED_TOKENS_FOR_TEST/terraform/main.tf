# SYNTHETIC TEST CREDENTIALS — NOT REAL

provider "aws" {
  region     = "us-east-1"
  access_key = "AKIAI44QH8DHBEXAMPLE"
  secret_key = "je7MtGbClwBF/2Zp9Utk/h3yCo8nvbEXAMPLEKEY"
}

resource "heroku_app" "production" {
  name   = "my-cool-app"
  region = "us"
}

resource "heroku_addon" "database" {
  app  = heroku_app.production.name
  plan = "heroku-postgresql:mini"
}

# Heroku API key stored inline (bad practice — for test purposes)
# heroku api_key = A1B2C3D4-E5F6-7890-ABCD-EF1234567890
variable "heroku_api_key" {
  default = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
}

resource "aws_db_instance" "main" {
  identifier        = "prod-db"
  engine            = "postgres"
  instance_class    = "db.t3.micro"
  username          = "dbadmin"
  password          = "Pr0d-DB-P@ssw0rd-2024!"
}
</content>
</invoke>