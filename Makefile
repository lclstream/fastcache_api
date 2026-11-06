# Pull fastcache api's mTLS certs from Vault onto the api host.
# cert-manager issues them; lclstream/lclstream-deploy pushes them to Vault.
#   make vault-pull   # ca.crt, server.crt, server.key -> certs/
#   make clean-certs  # remove the certs/ directory

CERT_DIR = certs
CERT_FILES = ca.crt server.crt server.key

VAULT_ADDR ?= https://vault.slac.stanford.edu
VAULT_SECRET_PATH ?= lcls/lcls-dataminer/lclstream-dev/fastcache-server

.PHONY: clean-certs vault-pull

vault-pull:
	@if vault token lookup -address=$(VAULT_ADDR) > /dev/null 2>&1; then \
		echo "using existing token"; \
	else \
		echo "Note: you must log in to vault"; \
		vault login -method=oidc -address=$(VAULT_ADDR); \
	fi
	@echo "Pulling certs from $(VAULT_ADDR) secret/$(VAULT_SECRET_PATH)"
	@mkdir -p $(CERT_DIR)
	@set -e; \
		tmp_dir=$$(mktemp -d "$(CERT_DIR)/.vault-pull.XXXXXX"); \
		trap 'rm -rf "$$tmp_dir"' EXIT; \
		for f in $(CERT_FILES); do \
			vault kv get -address=$(VAULT_ADDR) -field=$$f -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/$$f"; \
			chmod 400 "$$tmp_dir/$$f"; \
		done; \
		for f in $(CERT_FILES); do mv -f "$$tmp_dir/$$f" $(CERT_DIR)/; done; \
		trap - EXIT; \
		rmdir "$$tmp_dir"
	@echo "Done. Restart to load them: systemctl --user restart fastcache-api.service"

clean-certs:
	rm -rf $(CERT_DIR)
