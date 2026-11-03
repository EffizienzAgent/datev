app_name = "datev"
app_title = "DATEV"
app_publisher = "EffizienzAgent (Tobias Mende)"
app_description = (
	"DATEV integration for the Frappe Framework: documents with Beleglink, EXTF and DATEVconnect"
)
app_email = "oss@effizienzagent.de"
app_license = "mit"
app_logo_url = "/assets/datev/images/datev-logo.svg"

add_to_apps_screen = [
	{
		"name": "datev",
		"logo": "/assets/datev/images/datev-logo.svg",
		"title": "DATEV",
		"route": "/app/datev",
	}
]

after_install = "datev.setup.apply_defaults"
after_migrate = "datev.setup.apply_defaults"

datev_storage_connectors = {
	"Business OS": "datev.connectors.business_os.BusinessOSStorage",
	"E-mail to DUo": "datev.connectors.duo_email.DuoEmailStorage",
}
datev_export_connectors = {
	"DATEVconnect": "datev.connectors.datevconnect.DATEVconnectExport",
	"EXTF download": "datev.connectors.extf.ExtfDownloadExport",
	"EXTF e-mail": "datev.connectors.extf.ExtfEmailExport",
}
datev_master_data_connectors = {
	"DATEVconnect": "datev.connectors.datevconnect.DATEVconnectMasterData",
	"EXTF": "datev.connectors.extf.ExtfMasterData",
}

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["datev.documents.retry_due"],
		"0 * * * *": ["datev.dispatch.run", "datev.notify.check"],
	},
}
