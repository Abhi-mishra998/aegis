{{/*
ACP Helm Chart — Template Helpers
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "acp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncate at 63 chars because some Kubernetes name fields are limited to this.
*/}}
{{- define "acp.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "acp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Resolve the namespace for all resources.
*/}}
{{- define "acp.namespace" -}}
{{- if .Values.namespaceOverride }}
{{- .Values.namespaceOverride }}
{{- else }}
{{- .Release.Namespace }}
{{- end }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "acp.labels" -}}
helm.sh/chart: {{ include "acp.chart" . }}
{{ include "acp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels (stable set — do NOT add mutable labels here).
*/}}
{{- define "acp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "acp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component labels helper — call with (dict "component" "gateway" "ctx" $)
*/}}
{{- define "acp.componentLabels" -}}
{{ include "acp.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component selector labels — call with (dict "component" "gateway" "ctx" $)
*/}}
{{- define "acp.componentSelectorLabels" -}}
{{ include "acp.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "acp.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "acp.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Resolve image reference for a given service component.
Usage: include "acp.image" (dict "component" .Values.gateway "ctx" $)
*/}}
{{- define "acp.image" -}}
{{- $registry := .ctx.Values.global.image.registry -}}
{{- $tag := .ctx.Values.global.image.tag -}}
{{- $name := .component.image.name -}}
{{- printf "%s/%s:%s" $registry $name $tag }}
{{- end }}

{{/*
Image pull policy from global.
*/}}
{{- define "acp.imagePullPolicy" -}}
{{ .Values.global.image.pullPolicy }}
{{- end }}

{{/*
Render global env vars as a list of env entries.
*/}}
{{- define "acp.globalEnv" -}}
{{- range $key, $val := .Values.global.env }}
- name: {{ $key }}
  value: {{ $val | quote }}
{{- end }}
{{- end }}

{{/*
Reference to the ACP secrets (pre-created or ESO-managed).
Injects JWT_SECRET_KEY, INTERNAL_SECRET, GROQ_API_KEY from the secret.
*/}}
{{- define "acp.secretEnvRefs" -}}
- name: JWT_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: JWT_SECRET_KEY
- name: INTERNAL_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: INTERNAL_SECRET
- name: GROQ_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecret }}
      key: GROQ_API_KEY
      optional: true
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgresql.existingSecret }}
      key: {{ .Values.postgresql.existingSecretDatabaseUrlKey }}
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.redis.existingSecret }}
      key: {{ .Values.redis.existingSecretRedisUrlKey }}
{{- end }}

{{/*
Pod security context (shared across all services).
*/}}
{{- define "acp.podSecurityContext" -}}
{{- toYaml .Values.podSecurityContext }}
{{- end }}

{{/*
Container security context (shared across all services).
*/}}
{{- define "acp.containerSecurityContext" -}}
{{- toYaml .Values.containerSecurityContext }}
{{- end }}

{{/*
Standard tmpdir volume + volumeMount for read-only root filesystem.
Many Python/FastAPI apps need /tmp for temp files.
*/}}
{{- define "acp.tmpVolume" -}}
- name: tmp
  emptyDir: {}
{{- end }}

{{- define "acp.tmpVolumeMount" -}}
- name: tmp
  mountPath: /tmp
{{- end }}
