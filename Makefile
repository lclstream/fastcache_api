# Self-signed certificate generation + Vault publishing for fastcache api (mTLS).
#   make certs        # generate CA, server, and client certs into certs/
#   make verify-certs # verify the generated chain locally
#   make vault-push   # verify, then push them to Vault from the cert builder
#   make vault-pull   # pull them from Vault onto the fastcache_api host
#   make clean-certs  # remove the certs/ directory

CERT_DIR = certs
CERT_DAYS ?= 180
CERT_HOST ?= sdfdtn002.sdf.slac.stanford.edu
CERT_VALIDITY = $(shell echo $$(($(CERT_DAYS) * 24))h)

VAULT_ADDR ?= https://vault.slac.stanford.edu
VAULT_SECRET_PATH ?= lcls/psdm/lclstream/dev

.PHONY: certs ca server client clean-certs check-certs verify-certs vault-push vault-pull

certs: ca server client

ca: $(CERT_DIR)/ca.crt

$(CERT_DIR)/ca.crt:
	mkdir -p $(CERT_DIR)
	step certificate create "fastcache-ca" $(CERT_DIR)/ca.crt $(CERT_DIR)/ca.key \
		--profile root-ca --not-after $(CERT_VALIDITY) --no-password --insecure -f
	chmod 400 $(CERT_DIR)/ca.key $(CERT_DIR)/ca.crt

server: $(CERT_DIR)/server.crt

$(CERT_DIR)/server.crt: $(CERT_DIR)/ca.crt
	step certificate create "$(CERT_HOST)" $(CERT_DIR)/server.crt $(CERT_DIR)/server.key \
		--profile leaf --ca $(CERT_DIR)/ca.crt --ca-key $(CERT_DIR)/ca.key \
		--san $(CERT_HOST) --not-after $(CERT_VALIDITY) --no-password --insecure -f
	chmod 400 $(CERT_DIR)/server.key $(CERT_DIR)/server.crt

client: $(CERT_DIR)/client.crt

$(CERT_DIR)/client.crt: $(CERT_DIR)/ca.crt
	step certificate create "lclstream-client" $(CERT_DIR)/client.crt $(CERT_DIR)/client.key \
		--profile leaf --ca $(CERT_DIR)/ca.crt --ca-key $(CERT_DIR)/ca.key \
		--not-after $(CERT_VALIDITY) --no-password --insecure -f
	chmod 400 $(CERT_DIR)/client.key $(CERT_DIR)/client.crt

check-certs:
	@for f in ca.crt server.crt server.key client.crt client.key; do \
		test -f $(CERT_DIR)/$$f || { echo "missing $(CERT_DIR)/$$f (run: make certs)"; exit 1; }; \
	done

verify-certs: check-certs
	step certificate verify $(CERT_DIR)/server.crt --roots $(CERT_DIR)/ca.crt
	step certificate verify $(CERT_DIR)/client.crt --roots $(CERT_DIR)/ca.crt

vault-push: verify-certs
	@if vault token lookup -address=$(VAULT_ADDR) > /dev/null 2>&1; then \
		echo "using existing token"; \
	else \
		echo "Note: you must log in to vault"; \
		vault login -method=oidc -address=$(VAULT_ADDR); \
	fi
	@echo "Pushing certs to $(VAULT_ADDR) secret/$(VAULT_SECRET_PATH)"
	@vault kv patch -address=$(VAULT_ADDR) -mount=secret $(VAULT_SECRET_PATH) \
		ca.crt=@$(CERT_DIR)/ca.crt \
		server.crt=@$(CERT_DIR)/server.crt \
		server.key=@$(CERT_DIR)/server.key \
		client.crt=@$(CERT_DIR)/client.crt \
		client.key=@$(CERT_DIR)/client.key
	@echo "Done. ca.key was NOT pushed (stays local only)."

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
		vault kv get -address=$(VAULT_ADDR) -field=ca.crt -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/ca.crt"; \
		vault kv get -address=$(VAULT_ADDR) -field=server.crt -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/server.crt"; \
		vault kv get -address=$(VAULT_ADDR) -field=server.key -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/server.key"; \
		vault kv get -address=$(VAULT_ADDR) -field=client.crt -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/client.crt"; \
		vault kv get -address=$(VAULT_ADDR) -field=client.key -mount=secret $(VAULT_SECRET_PATH) > "$$tmp_dir/client.key"; \
		chmod 400 "$$tmp_dir"/ca.crt "$$tmp_dir"/server.crt "$$tmp_dir"/server.key "$$tmp_dir"/client.crt "$$tmp_dir"/client.key; \
		mv -f "$$tmp_dir"/ca.crt "$$tmp_dir"/server.crt "$$tmp_dir"/server.key "$$tmp_dir"/client.crt "$$tmp_dir"/client.key $(CERT_DIR)/; \
		trap - EXIT; \
		rmdir "$$tmp_dir"
	@echo "Done. ca.key was NOT pulled; keep it only on the cert builder."

clean-certs:
	rm -rf $(CERT_DIR)
