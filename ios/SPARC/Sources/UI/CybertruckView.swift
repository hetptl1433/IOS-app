import SwiftUI

/// Keep the approved truck art for that model; other vehicles use an honest
/// generic silhouette until their own assets are available.
struct ControllerVehicleView: View {
    @EnvironmentObject private var store: AppStore
    let charging: Bool
    var body: some View {
        if store.usesCybertruckArtwork {
            CybertruckView(charging: charging)
        } else {
            ZStack {
                Color.clear.aspectRatio(1.5, contentMode: .fit)
                Image(systemName: "car.side.fill").resizable().scaledToFit()
                    .foregroundStyle(LinearGradient(colors: [.white.opacity(0.9), Color(white: 0.4)], startPoint: .top, endPoint: .bottom))
                    .padding(.horizontal, 24).padding(.vertical, 34)
                if charging {
                    Image(systemName: "bolt.fill").font(.system(size: 20, weight: .bold))
                        .foregroundStyle(SPARCTheme.spark).offset(y: 35)
                }
            }
            .accessibilityLabel("\(store.displayVehicleName), generic vehicle illustration\(charging ? ", charging" : "")")
            .accessibilityIdentifier("vehicle.genericModel")
        }
    }
}

/// The light uses the same normalized coordinates as the approved cutout asset.
/// The image's light bar is off; only an actual charging state adds illumination.
struct CybertruckView: View {
    let charging: Bool

    var body: some View {
        Image("Cybertruck")
            .resizable()
            .aspectRatio(1.5, contentMode: .fit)
            .overlay {
                if charging { CybertruckLightGlow() }
            }
            .accessibilityLabel("Silver Cybertruck, front light \(charging ? "illuminated while charging" : "off")")
            .accessibilityIdentifier("cybertruck.model")
    }
}

/// A soft white line along the front light bar. It breathes slowly (4 s, 60% to 100%) and only exists while
/// charging, so its animation state starts fresh each time charging begins and is gone when it ends.
private struct CybertruckLightGlow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var bright = false

    var body: some View {
        GeometryReader { proxy in
            // Stroke widths follow the approved 1536 pt artwork so the light looks the same at any size.
            let scale = proxy.size.width / 1536
            ZStack {
                CybertruckLightBar()
                    .stroke(.white, style: StrokeStyle(lineWidth: max(2.8, 24 * scale), lineCap: .round, lineJoin: .round))
                    .blur(radius: max(2, 10 * scale))
                    .opacity(reduceMotion ? 1 : (bright ? 1 : 0.6))
                CybertruckLightBar()
                    .stroke(.white, style: StrokeStyle(lineWidth: max(1, 5 * scale), lineCap: .round, lineJoin: .round))
            }
        }
        .accessibilityHidden(true)
        .onAppear { startBreathing() }
        .onChange(of: reduceMotion) { startBreathing() }
    }

    private func startBreathing() {
        var reset = Transaction(animation: nil)
        reset.disablesAnimations = true
        withTransaction(reset) { bright = false }
        guard !reduceMotion else { return }
        withAnimation(.easeInOut(duration: 4).repeatForever(autoreverses: true)) { bright = true }
    }
}

private struct CybertruckLightBar: Shape {
    func path(in rect: CGRect) -> Path {
        let points: [CGPoint] = [
            CGPoint(x: 946, y: 481), CGPoint(x: 1094, y: 500),
            CGPoint(x: 1260, y: 502.5), CGPoint(x: 1472, y: 503), CGPoint(x: 1511, y: 490)
        ]
        var path = Path()
        for (index, point) in points.enumerated() {
            let point = CGPoint(x: rect.minX + point.x / 1536 * rect.width, y: rect.minY + point.y / 1024 * rect.height)
            if index == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        return path
    }
}
