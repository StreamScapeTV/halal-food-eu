import SwiftUI
import UIKit

struct SettingsView: View {
    @Bindable var preferences: AppPreferences
    @Bindable var catalogModules: CatalogModuleSettingsModel
    let identity: AppRuntimeIdentity
    let onOpenSaved: () -> Void

    @Environment(\.openURL) private var openURL

    var body: some View {
        Form {
            marketSection
            appearanceSection
            languageSection
            privacySection
            aboutSection
        }
        .navigationTitle(String(localized: "Settings", table: "AppShell"))
        .alert(
            String(localized: "Catalog update unavailable", table: "AppShell"),
            isPresented: Binding(
                get: { catalogModules.errorMessage != nil },
                set: { if !$0 { catalogModules.errorMessage = nil } }
            )
        ) {
            Button(String(localized: "OK", table: "AppShell"), role: .cancel) {
                catalogModules.errorMessage = nil
            }
        } message: {
            Text(catalogModules.errorMessage ?? "")
        }
    }

    private var marketSection: some View {
        Section {
            Picker(
                String(localized: "Active market", table: "AppShell"),
                selection: Binding(
                    get: { catalogModules.selectedMarket },
                    set: { market in Task { await catalogModules.selectMarket(market) } }
                )
            ) {
                ForEach(catalogModules.installedSelectableMarkets) { market in
                    Text(market.rawValue).tag(market)
                }
            }
            .disabled(catalogModules.isWorking)

            LabeledContent(String(localized: "Active catalog", table: "AppShell")) {
                Text(catalogModules.activeCatalogVersion ?? unavailableText)
                    .monospaced()
            }

            if catalogModules.usesBundledGermanyFallback {
                Text(String(localized: "Using the bundled Germany catalog. Normal lookup stays fully offline.", table: "AppShell"))
                    .foregroundStyle(.secondary)
            } else if let bytes = catalogModules.activeInstalledBytes {
                LabeledContent(String(localized: "Installed size", table: "AppShell")) {
                    Text(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
                }
            }

            ForEach(catalogModules.activeCoverageLimitations, id: \.self) { limitation in
                Label(limitation, systemImage: "info.circle")
                    .foregroundStyle(.secondary)
            }

            Button {
                Task { await catalogModules.checkAvailableMarkets() }
            } label: {
                Label(
                    String(localized: "Check for signed market catalogs", table: "AppShell"),
                    systemImage: "arrow.clockwise"
                )
            }
            .disabled(catalogModules.isWorking)

            ForEach(catalogModules.downloadableMarkets) { market in
                Button {
                    Task { await catalogModules.installLatest(for: market) }
                } label: {
                    Label(
                        String(
                            format: String(localized: "Download verified %@ catalog", table: "AppShell"),
                            locale: .current,
                            market.rawValue
                        ),
                        systemImage: "arrow.down.circle"
                    )
                }
                .disabled(catalogModules.isWorking)
            }

            if catalogModules.canRemoveActiveDownload {
                Button(role: .destructive) {
                    Task { await catalogModules.removeActiveDownload() }
                } label: {
                    Text(String(localized: "Remove downloaded catalog", table: "AppShell"))
                }
                .disabled(catalogModules.isWorking)
            }

            if catalogModules.isWorking {
                HStack(spacing: 10) {
                    ProgressView()
                    Text(String(localized: "Verifying catalog…", table: "AppShell"))
                }
                .accessibilityElement(children: .combine)
            } else if let status = catalogModules.statusMessage {
                Text(status).foregroundStyle(.secondary)
            }
        } header: {
            Text(String(localized: "Market catalog", table: "AppShell"))
        } footer: {
            Text(String(localized: "Downloaded market catalogs are optional. They are verified before activation, normal lookup remains offline, and Germany always keeps the bundled fallback.", table: "AppShell"))
        }
    }

    private var appearanceSection: some View {
        Section {
            Picker(
                String(localized: "Appearance", table: "AppShell"),
                selection: $preferences.appearance
            ) {
                Text(String(localized: "System", table: "AppShell")).tag(AppAppearance.system)
                Text(String(localized: "Light", table: "AppShell")).tag(AppAppearance.light)
                Text(String(localized: "Dark", table: "AppShell")).tag(AppAppearance.dark)
            }
            .accessibilityHint(
                String(localized: "Uses the selected system color scheme without changing product evidence or assessments.", table: "AppShell")
            )
        } header: {
            Text(String(localized: "Appearance", table: "AppShell"))
        }
    }

    private var languageSection: some View {
        Section {
            Button {
                openURL(AppSettingsLink.url)
            } label: {
                Label(String(localized: "Open iOS app settings", table: "AppShell"), systemImage: "globe")
            }
            .accessibilityHint(
                String(localized: "Opens the system settings where iOS manages this app's language.", table: "AppShell")
            )
        } header: {
            Text(String(localized: "Language", table: "AppShell"))
        } footer: {
            Text(String(localized: "Halal Food EU follows the app language selected in iOS. English and German are available; there is no in-app language override.", table: "AppShell"))
        }
    }

    private var privacySection: some View {
        Section {
            Text(String(localized: "The bundled Germany catalog and every installed verified market catalog work fully offline after installation. Optional update checks send no scans, searches, history, account or location identifiers.", table: "AppShell"))
            Text(String(localized: "Ingredient OCR runs on device; its captured image and recognized text are not stored by the app.", table: "AppShell"))
            Text(String(localized: "Favorites and optional scan history stay local. The app has no accounts, analytics, advertising, or tracking.", table: "AppShell"))
            Button(action: onOpenSaved) {
                Label(String(localized: "Manage saved products and history", table: "AppShell"), systemImage: "star")
            }
            .accessibilityHint(
                String(localized: "Switches to Saved where favorites and optional scan history can be managed.", table: "AppShell")
            )
        } header: {
            Text(String(localized: "Privacy and local data", table: "AppShell"))
        }
    }

    private var aboutSection: some View {
        Section {
            LabeledContent(String(localized: "Version", table: "AppShell")) { Text(identity.version ?? unavailableText) }
            LabeledContent(String(localized: "Build", table: "AppShell")) { Text(identity.build ?? unavailableText) }
            LabeledContent(String(localized: "Catalog version", table: "AppShell")) {
                Text(catalogModules.activeCatalogVersion ?? identity.catalogVersion ?? unavailableText).monospaced()
            }
        } header: {
            Text(String(localized: "About", table: "AppShell"))
        }
    }

    private var unavailableText: String {
        String(localized: "Unavailable", table: "AppShell")
    }
}

@MainActor
enum AppSettingsLink {
    static let url = URL(string: UIApplication.openSettingsURLString)!
}
