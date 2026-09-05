import SwiftUI
import UIKit

struct SettingsView: View {
    @Bindable var preferences: AppPreferences
    let identity: AppRuntimeIdentity
    let onOpenSaved: () -> Void

    @Environment(\.openURL) private var openURL

    var body: some View {
        Form {
            Section {
                Picker(
                    String(localized: "Appearance", table: "AppShell"),
                    selection: $preferences.appearance
                ) {
                    Text(String(localized: "System", table: "AppShell"))
                        .tag(AppAppearance.system)
                    Text(String(localized: "Light", table: "AppShell"))
                        .tag(AppAppearance.light)
                    Text(String(localized: "Dark", table: "AppShell"))
                        .tag(AppAppearance.dark)
                }
                .accessibilityHint(
                    String(
                        localized: "Uses the selected system color scheme without changing product evidence or assessments.",
                        table: "AppShell"
                    )
                )
            } header: {
                Text(String(localized: "Appearance", table: "AppShell"))
            }

            Section {
                Button {
                    openURL(AppSettingsLink.url)
                } label: {
                    Label(
                        String(localized: "Open iOS app settings", table: "AppShell"),
                        systemImage: "globe"
                    )
                }
                .accessibilityHint(
                    String(
                        localized: "Opens the system settings where iOS manages this app's language.",
                        table: "AppShell"
                    )
                )
            } header: {
                Text(String(localized: "Language", table: "AppShell"))
            } footer: {
                Text(
                    String(
                        localized: "Halal Food EU follows the app language selected in iOS. English and German are available; there is no in-app language override.",
                        table: "AppShell"
                    )
                )
            }

            Section {
                Text(
                    String(
                        localized: "Catalog lookups and product evidence use the catalog bundled with the app and do not require a network connection.",
                        table: "AppShell"
                    )
                )
                Text(
                    String(
                        localized: "Ingredient OCR runs on device; its captured image and recognized text are not stored by the app.",
                        table: "AppShell"
                    )
                )
                Text(
                    String(
                        localized: "Favorites and optional scan history stay local. The app has no accounts, analytics, advertising, or tracking.",
                        table: "AppShell"
                    )
                )
                Button(action: onOpenSaved) {
                    Label(
                        String(localized: "Manage saved products and history", table: "AppShell"),
                        systemImage: "star"
                    )
                }
                .accessibilityHint(
                    String(
                        localized: "Switches to Saved where favorites and optional scan history can be managed.",
                        table: "AppShell"
                    )
                )
            } header: {
                Text(String(localized: "Privacy and local data", table: "AppShell"))
            }

            Section {
                LabeledContent(String(localized: "Version", table: "AppShell")) {
                    Text(identity.version ?? unavailableText)
                }
                LabeledContent(String(localized: "Build", table: "AppShell")) {
                    Text(identity.build ?? unavailableText)
                }
                LabeledContent(String(localized: "Catalog version", table: "AppShell")) {
                    Text(identity.catalogVersion ?? unavailableText)
                        .monospaced()
                }
            } header: {
                Text(String(localized: "About", table: "AppShell"))
            }
        }
        .navigationTitle(String(localized: "Settings", table: "AppShell"))
    }

    private var unavailableText: String {
        String(localized: "Unavailable", table: "AppShell")
    }
}

@MainActor
enum AppSettingsLink {
    static let url = URL(string: UIApplication.openSettingsURLString)!
}
