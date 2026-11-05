targetScope = 'resourceGroup'

@description('AZD environment name used to name resources.')
param name string

@description('Azure region for all resources.')
param location string

@description('Common AZD resource tags.')
param tags object = {}

@description('GitHub token with Copilot access. Stored in Key Vault and read by the app through its managed identity.')
@secure()
param copilotGithubToken string

@description('Copilot model name. Empty uses the default model.')
param copilotModel string = ''

@description('Image used while provisioning. azd replaces it during deploy.')
param appImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var abbreviations = loadJsonContent('../abbreviations.json')
var suffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var registryName = replace(toLower('${abbreviations.containerRegistry}${name}${suffix}'), '-', '')
var logAnalyticsName = toLower('${abbreviations.logAnalyticsWorkspace}-${name}-${suffix}')
var insightsName = toLower('${abbreviations.applicationInsights}-${name}-${suffix}')
var keyVaultName = take(replace(toLower('${abbreviations.keyVault}${name}${suffix}'), '-', ''), 24)
var environmentName = toLower('${abbreviations.containerAppEnvironment}-${name}-${suffix}')
var appName = take(toLower('${abbreviations.containerApp}-${name}-app'), 32)
var identityName = toLower('${abbreviations.userAssignedIdentity}-${name}-app')
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
  tags: tags
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
  tags: tags
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
  tags: tags
}

resource copilotTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'copilot-github-token'
  properties: {
    value: copilotGithubToken
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
  tags: tags
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
  tags: tags
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource keyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, 'keyvault-secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'acrpull')
  scope: registry
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'copilot-github-token'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/copilot-github-token'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: appImage
          env: [
            {
              name: 'COPILOT_GITHUB_TOKEN'
              secretRef: 'copilot-github-token'
            }
            {
              name: 'COPILOT_MODEL'
              value: copilotModel
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'biseosil'
            }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '4'
              }
            }
          }
        ]
      }
    }
  }
  tags: union(tags, {
    'azd-service-name': 'app'
  })
  dependsOn: [
    copilotTokenSecret
    keyVaultAccess
    acrPull
  ]
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output registryLoginServer string = registry.properties.loginServer
output keyVaultName string = keyVault.name
