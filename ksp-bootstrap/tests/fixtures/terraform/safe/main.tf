terraform {
  required_providers {
    contabo = {
      source  = "contabo/contabo"
      version = "~> 0.2"
    }
  }
}

resource "contabo_instance" "example" {
  display_name = "fixture-only"
}
