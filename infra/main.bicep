targetScope = 'subscription'

@description('AZD environment name used to name resources.')
param environmentName string

@description('Azure region for all resources.')
param location string

@description('GitHub token with Copilot access. Stored only in Key Vault. azd reads COPILOT_GITHUB_TOKEN from the environment.')
@secure()
param copilotGithubToken string

@description('Copilot model name. Empty uses the default model.')
param copilotModel string = ''

var abbreviations = loadJsonContent('./abbreviations.json')
var tags = {
  'azd-env-name': environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${abbreviations.resourceGroup}-${environmentName}'
  location: location
  tags: tags
}

module application './modules/application.bicep' = {
  name: 'application'
  scope: resourceGroup
  params: {
    name: environmentName
    location: location
    tags: tags
    copilotGithubToken: copilotGithubToken
    copilotModel: copilotModel
  }
}

output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = application.outputs.registryLoginServer
output AZURE_KEY_VAULT_NAME string = application.outputs.keyVaultName
output APP_URL string = application.outputs.appUrl
output APP_HEALTH_URL string = '${application.outputs.appUrl}/health'
