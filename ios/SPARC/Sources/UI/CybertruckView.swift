import SwiftUI

/// The light uses the same normalized coordinates as the approved cutout asset.
/// The image's light bar is off; only an actual charging state adds illumination.
struct CybertruckView: View {
    let charging: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false

    var body: some View {
        Image("Cybertruck")
            .resizable()
            .aspectRatio(1.5, contentMode: .fit)
            .overlay {
                if charging {
                    CybertruckLightBar()
                        .stroke(.white.opacity(reduceMotion ? 0.6 : (breathing ? 0.82 : 0.38)), style: StrokeStyle(lineWidth: 2.8, lineCap: .round, lineJoin: .round))
                        .blur(radius: 2)
                        .overlay {
                            CybertruckLightBar()
                                .stroke(.white.opacity(0.96), style: StrokeStyle(lineWidth: 1.0, lineCap: .round, lineJoin: .round))
                        }
                        .transition(.opacity)
                }
            }
            .onAppear { updateBreathing() }
            .onChange(of: charging) { updateBreathing() }
            .onChange(of: reduceMotion) { updateBreathing() }
            .accessibilityLabel("Silver Cybertruck, front light \(charging ? "illuminated while charging" : "off")")
            .accessibilityIdentifier("cybertruck.model")
    }

    private func updateBreathing() {
        breathing = false
        if charging && !reduceMotion {
            withAnimation(.easeInOut(duration: 1.7).repeatForever(autoreverses: true)) { breathing = true }
        }
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
