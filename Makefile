# Makefile
# Version file lives in repo root, template adds the leading "v" in UI.
VERSION_FILE := VERSION

# Discover current version: prefer VERSION file; else exact tag; else 0.0.0
CURRENT_VERSION := $(shell \
  [ -f $(VERSION_FILE) ] && cat $(VERSION_FILE) || \
  (git describe --tags --exact-match 2>/dev/null | sed -E 's/^v//' ) || \
  echo 0.0.0 \
)

.PHONY: release release-patch release-minor release-major show-version

show-version:
	@echo "Current version: $(CURRENT_VERSION)"

# Manual release: pass VERSION=X.Y.Z (no leading 'v', since template adds it)
release:
	@test -n "$(VERSION)" || (echo "ERROR: pass VERSION=X.Y.Z (e.g., make release VERSION=1.2.1)"; exit 1)
	@echo "$(VERSION)" > $(VERSION_FILE)
	git add $(VERSION_FILE)
	git commit -m "chore: release v$(VERSION)" || true
	git tag -a "v$(VERSION)" -m "v$(VERSION)"
	git push
	git push --tags
	@echo "Released v$(VERSION)"

# Auto-bump helpers (no args). They read CURRENT_VERSION, bump, then call `release`.
release-patch:
	@NEXT=$$(python3 - <<'PY'
import sys
v = "$(CURRENT_VERSION)".strip() or "0.0.0"
parts = [int(x) for x in v.split(".")[:3] + ["0","0","0"]][:3]
parts[2] += 1
print(".".join(map(str, parts)))
PY
); \
	$(MAKE) release VERSION="$$NEXT"

release-minor:
	@NEXT=$$(python3 - <<'PY'
import sys
v = "$(CURRENT_VERSION)".strip() or "0.0.0"
maj,minor,patch = (list(map(int,(v.split(".")+["0","0"])[:3])))
minor += 1; patch = 0
print(f"{maj}.{minor}.{patch}")
PY
); \
	$(MAKE) release VERSION="$$NEXT"

release-major:
	@NEXT=$$(python3 - <<'PY'
import sys
v = "$(CURRENT_VERSION)".strip() or "0.0.0"
maj,minor,patch = (list(map(int,(v.split(".")+["0","0"])[:3])))
maj += 1; minor = 0; patch = 0
print(f"{maj}.{minor}.{patch}")
PY
); \
	$(MAKE) release VERSION="$$NEXT"
