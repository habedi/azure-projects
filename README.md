## Microsoft Azure Projects

[![Tests](https://img.shields.io/github/actions/workflow/status/habedi/azure-projects/tests.yml?label=tests&style=flat&labelColor=333333&logo=github&logoColor=white)](https://github.com/habedi/azure-projects/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache_2.0-007ec6?style=flat&labelColor=333333&logo=apache&logoColor=white)](LICENSE)

---

This repository contains projects for learning more about Microsoft Azure services, testing different use cases, and developing cloud applications
in Azure.

---

### Quickstart

To follow the steps below, you need to have Git and [Nix](https://nixos.org/download/) (with flakes enabled) installed on your machine.

#### 1. Clone the repository

```bash
git clone https://github.com/habedi/azure-projects.git
cd azure-projects
```

#### 2. Set up the development environment

```shell
nix develop
```

#### 3. Check out the projects

Check out the contents of the [projects](projects) directory for the Azure projects and their documentation.

| #  | Project Name                                          | Complexity   |
|----|-------------------------------------------------------|--------------|
| 01 | [Blob storage round trip](projects/01-blob-storage)   | Beginner     |
| 02 | [A medallion lakehouse](projects/02-fabric-lakehouse) | Intermediate |

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
