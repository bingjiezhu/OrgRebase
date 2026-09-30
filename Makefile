.PHONY: check-core public-release public-release-check
RELEASE_DIR ?=

check-core:
	$(MAKE) -C orgrebase check-core

# Create a fresh, immutable public iteration from the canonical workspace.
public-release:
	@test -n "$(RELEASE_DIR)" || { printf '%s\n' 'Set RELEASE_DIR to a new iteration directory.'; exit 2; }
	python3 -B orgrebase/scripts/export_public_release.py build --workspace . --output "$(RELEASE_DIR)"

public-release-check:
	@test -n "$(RELEASE_DIR)" || { printf '%s\n' 'Set RELEASE_DIR to the saved iteration directory.'; exit 2; }
	python3 -B orgrebase/scripts/export_public_release.py check --workspace . --release "$(RELEASE_DIR)"
