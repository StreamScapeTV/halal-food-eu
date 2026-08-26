.PHONY: generate catalog catalog-validate ci clean

generate: catalog
	xcodegen generate

catalog:
	python3 Tools/catalog_builder.py \
		--input Data/sample-products.json \
		--database HalalFoodEU/Resources/catalog.sqlite3 \
		--manifest HalalFoodEU/Resources/catalog-manifest.json

catalog-validate:
	python3 Tools/validate_catalog.py \
		--database HalalFoodEU/Resources/catalog.sqlite3 \
		--manifest HalalFoodEU/Resources/catalog-manifest.json \
		--source Data/sample-products.json

ci: catalog-validate
	./Scripts/ci-ios.sh

clean:
	rm -rf HalalFoodEU.xcodeproj .build DerivedData
