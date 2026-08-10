# terraform

実 AWS（`ap-northeast-1`）に当てるルート。state は S3。

手順は [docs/040_practice/0010-env.md](../docs/040_practice/0010-env.md) から番号順。

```bash
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=backend.hcl
terraform apply
```
