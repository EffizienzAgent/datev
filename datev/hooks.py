app_name = "datev"
app_title = "DATEV"
app_publisher = "EffizienzAgent (Tobias Mende)"
app_description = (
	"DATEV integration for the Frappe Framework: documents with Beleglink, EXTF and DATEVconnect"
)
app_email = "oss@effizienzagent.de"
app_license = "mit"

after_install = "datev.setup.apply_defaults"
after_migrate = "datev.setup.apply_defaults"
