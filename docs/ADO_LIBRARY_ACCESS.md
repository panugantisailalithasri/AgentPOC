# Giving the agent ADO Library (variable group) read access

The Cursor ADO MCP **does not** expose Library/variable-group tools yet (even with a full-read PAT).
Use one of these paths:

## Option A — recommended (you run, agent reads redacted file)

From PowerShell in the project folder:

```powershell
cd C:\Users\PanugantiSailalithaS\deployment-verification-agent

# Raw PAT (Variable Groups Read is enough)
$env:ADO_PAT = "<paste-raw-pat-here>"

.\.venv\Scripts\python.exe scripts\fetch_ado_library_groups.py
```

This writes `fixtures/ado-library-groups-redacted.json` with secret values masked as `***SECRET***`.
Tell me when the file exists and I will map key names → verification fields.

## Option B — keep using Deployment Agent export

If Deployment Agent already fills `infra_export.variables` / `aws_infra_info_group`, no Library API is required for verification runs.

## Option C — Azure CLI (optional)

```powershell
az extension add --name azure-devops
az devops configure --defaults organization=https://dev.azure.com/FreyrDevOps project=Freyr-Unified-RIMS
echo $env:ADO_PAT | az devops login
az pipelines variable-group list -o table
az pipelines variable-group show --group-id <id> -o json > vg.json
```

## PAT scopes needed

- **Variable Groups → Read**
- **Project and Team → Read**
- (optional) Build/Release Read if you also want pipeline metadata

Do **not** paste the PAT into chat. Local env / mcp.json only.
