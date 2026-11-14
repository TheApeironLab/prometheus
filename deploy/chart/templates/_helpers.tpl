{{- define "prometheus.image" -}}
{{- $root := index . 0 -}}
{{- $role := index . 1 -}}
{{- $image := index $root.Values.images $role -}}
{{- $registry := required "global.imageRegistry must be injected by Vasi or set for administrator diagnostics" $root.Values.global.imageRegistry -}}
{{- if $image.digest -}}
{{- printf "%s/%s@%s" $registry $image.repository $image.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" $registry $image.repository $image.tag -}}
{{- end -}}
{{- end -}}
