import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Market catalog routing")
struct MarketCatalogRouterTests {
    @Test("The same GTIN resolves independently in explicitly selected markets")
    func separatesSameGTINAcrossMarkets() async throws {
        let barcode = try Barcode(validating: "4006381333931")
        let france = try CatalogMarket(validating: "FR")
        let german = product(barcode: barcode, name: "German formulation", market: .germany, version: "1.0.0")
        let french = product(barcode: barcode, name: "French formulation", market: france, version: "2.0.0")
        let router = MarketCatalogRouter(
            bundledGermany: .init(
                catalog: FixedMarketCatalog(product: german),
                searchCatalog: FixedMarketSearchCatalog(),
                catalogVersion: "1.0.0"
            )
        )

        await router.registerDownloadedModule(
            market: france,
            catalog: FixedMarketCatalog(product: french),
            searchCatalog: FixedMarketSearchCatalog(),
            catalogVersion: "2.0.0"
        )
        await router.selectMarket(france)
        let frenchResolution = try await router.resolveProduct(for: barcode)
        #expect(frenchResolution.market == france)
        #expect(frenchResolution.catalogVersion == "2.0.0")
        #expect(frenchResolution.product?.name == "French formulation")

        await router.selectMarket(.germany)
        let germanResolution = try await router.resolveProduct(for: barcode)
        #expect(germanResolution.market == .germany)
        #expect(germanResolution.catalogVersion == "1.0.0")
        #expect(germanResolution.product?.name == "German formulation")
    }

    @Test("A market switch invalidates an in-flight old-market lookup")
    func marketSwitchCancelsStaleResult() async throws {
        let barcode = try Barcode(validating: "4006381333931")
        let france = try CatalogMarket(validating: "FR")
        let suspended = SuspendedMarketCatalog(
            product: product(barcode: barcode, name: "Old DE result", market: .germany, version: "1.0.0")
        )
        let router = MarketCatalogRouter(
            bundledGermany: .init(
                catalog: suspended,
                searchCatalog: FixedMarketSearchCatalog(),
                catalogVersion: "1.0.0"
            )
        )
        await router.registerDownloadedModule(
            market: france,
            catalog: FixedMarketCatalog(
                product: product(barcode: barcode, name: "FR result", market: france, version: "2.0.0")
            ),
            searchCatalog: FixedMarketSearchCatalog(),
            catalogVersion: "2.0.0"
        )

        let task = Task { try await router.resolveProduct(for: barcode) }
        try await waitUntil { await suspended.isSuspended }
        await router.selectMarket(france)
        await suspended.resume()

        do {
            _ = try await task.value
            Issue.record("Expected stale old-market lookup to be cancelled")
        } catch is CancellationError {
            // Expected: an old-market result must never publish under the new market.
        }
    }

    @Test("An unsupported selected market stays active and never falls through to Germany")
    func unsupportedMarketFailsClosed() async throws {
        let france = try CatalogMarket(validating: "FR")
        let barcode = try Barcode(validating: "4006381333931")
        let router = MarketCatalogRouter(
            bundledGermany: .init(
                catalog: FixedMarketCatalog(product: nil),
                searchCatalog: FixedMarketSearchCatalog(),
                catalogVersion: "1.0.0"
            )
        )

        await router.selectMarket(france)
        #expect(await router.activeMarket() == france)

        do {
            _ = try await router.resolveProduct(for: barcode)
            Issue.record("Expected an uninstalled active market to be unavailable")
        } catch ProductCatalogError.unavailable(let message) {
            #expect(message.contains("FR"))
            #expect(await router.activeMarket() == france)
        }
    }

    private func product(
        barcode: Barcode,
        name: String,
        market: CatalogMarket,
        version: String
    ) -> ProductRecord {
        ProductRecord(
            barcode: barcode,
            name: name,
            brand: "Fixture",
            observation: nil,
            assessment: .unreviewedUnknown,
            catalogVersion: version,
            details: ProductRecordDetails(
                market: market.rawValue,
                brandOwner: nil,
                quantity: nil,
                conflictFlags: [],
                retailerEvidence: [],
                remoteImages: []
            )
        )
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @escaping @Sendable () async -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for suspended catalog lookup")
    }
}

private actor FixedMarketCatalog: ProductCatalog {
    let product: ProductRecord?
    init(product: ProductRecord?) { self.product = product }
    func product(for barcode: Barcode) async throws -> ProductRecord? { product }
}

private actor FixedMarketSearchCatalog: ProductSearchCatalog {
    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        ProductSearchPage(results: [], offset: 0, hasMore: false)
    }
}

private actor SuspendedMarketCatalog: ProductCatalog {
    let product: ProductRecord?
    private var resumed = false
    private(set) var isSuspended = false

    init(product: ProductRecord?) { self.product = product }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        isSuspended = true
        while !resumed {
            try await Task.sleep(for: .milliseconds(5))
        }
        isSuspended = false
        return product
    }

    func resume() { resumed = true }
}
