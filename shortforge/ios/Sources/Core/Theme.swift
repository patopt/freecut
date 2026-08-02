import SwiftUI

/// Design tokens shared with the web dashboard, so the two surfaces read as one
/// product. Values mirror `frontend/styles.css`.
enum Theme {
    static let accent = Color(red: 0.388, green: 0.400, blue: 0.945)
    static let accent2 = Color(red: 0.545, green: 0.361, blue: 0.965)
    static let success = Color(red: 0.063, green: 0.725, blue: 0.506)
    static let warning = Color(red: 0.961, green: 0.620, blue: 0.043)
    static let danger = Color(red: 0.937, green: 0.267, blue: 0.267)
    static let info = Color(red: 0.220, green: 0.741, blue: 0.973)

    static let accentGradient = LinearGradient(
        colors: [accent, accent2], startPoint: .topLeading, endPoint: .bottomTrailing)

    enum Radius {
        static let card: CGFloat = 18
        static let control: CGFloat = 12
        static let small: CGFloat = 9
    }

    enum Space {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 22
        static let xxl: CGFloat = 30
    }
}

/// The ambient background: a dark base with two soft accent blooms, matching the
/// web dashboard's radial gradients.
struct ForgeBackground: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack {
            (scheme == .dark ? Color(red: 0.031, green: 0.035, blue: 0.047)
                             : Color(red: 0.957, green: 0.965, blue: 0.980))
                .ignoresSafeArea()
            GeometryReader { geo in
                ZStack {
                    bloom(Theme.accent, opacity: scheme == .dark ? 0.16 : 0.20)
                        .frame(width: geo.size.width * 1.3)
                        .position(x: geo.size.width * 0.15, y: 0)
                    bloom(Theme.accent2, opacity: scheme == .dark ? 0.13 : 0.16)
                        .frame(width: geo.size.width * 1.1)
                        .position(x: geo.size.width, y: geo.size.height * 0.08)
                }
                .blur(radius: 60)
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)
        }
    }

    private func bloom(_ color: Color, opacity: Double) -> some View {
        Circle().fill(
            RadialGradient(colors: [color.opacity(opacity), .clear],
                           center: .center, startRadius: 0, endRadius: 320))
    }
}

/// An opaque content surface.
///
/// Deliberately *not* glass: Apple's guidance is that glass belongs to the
/// navigation layer floating above content, never to the content itself —
/// putting text on a translucent card is what wrecks contrast.
struct CardSurface: ViewModifier {
    var padding: CGFloat = Theme.Space.lg

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .fill(.background.secondary)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
            )
    }
}

/// The floating navigation material. `.ultraThinMaterial` is used rather than
/// iOS 26's `glassEffect` so the project still compiles against older SDKs on a
/// CI runner; on iOS 26 the system already renders this material as Liquid Glass.
struct GlassLayer: ViewModifier {
    var cornerRadius: CGFloat = Theme.Radius.card

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial,
                        in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(
                        LinearGradient(colors: [Color.white.opacity(0.28), Color.white.opacity(0.05)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing),
                        lineWidth: 0.8)
            )
            .shadow(color: .black.opacity(0.22), radius: 18, y: 8)
    }
}

extension View {
    func cardSurface(padding: CGFloat = Theme.Space.lg) -> some View {
        modifier(CardSurface(padding: padding))
    }

    func glassLayer(cornerRadius: CGFloat = Theme.Radius.card) -> some View {
        modifier(GlassLayer(cornerRadius: cornerRadius))
    }
}
