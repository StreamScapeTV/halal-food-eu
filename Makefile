.PHONY: generate catalog catalog-validate ci clean

generate: catalog
	xcodegen generate

catalog:
	PYTHONPATH=Tools python3 Tools/build_bundled_catalog.py \
		--source-manifest Data/catalog/bundled/de/source-manifest-v1.json \
		--database HalalFoodEU/Resources/catalog.sqlite3 \
		--manifest HalalFoodEU/Resources/catalog-manifest.json \
		--source-commit "$${HFEU_SOURCE_COMMIT:-0000000000000000000000000000000000000000}" \
		--workflow-run local-make

catalog-validate: catalog
	PYTHONPATH=Tools python3 Tools/production_catalog.py validate \
		--database HalalFoodEU/Resources/catalog.sqlite3 \
		--manifest HalalFoodEU/Resources/catalog-manifest.json
	PYTHONPATH=Tools python3 Tools/product_search_index.py validate \
		--database HalalFoodEU/Resources/catalog.sqlite3 \
		--manifest HalalFoodEU/Resources/catalog-manifest.json

ci: catalog-validate
	./Scripts/ci-ios.sh

clean:
	rm -rf HalalFoodEU.xcodeproj .build DerivedData
