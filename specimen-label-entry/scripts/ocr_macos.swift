import Foundation
import Vision
import AppKit

struct OCRLine: Codable {
    var text: String
    var confidence: Float
    var x: Double
    var y: Double
    var width: Double
    var height: Double
    var alternatives: [String]
}
struct OCRPage: Codable {
    var image: String
    var lines: [OCRLine]
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count >= 2 else {
    fputs("Usage: ocr_macos OUTPUT.json IMAGE...\n", stderr)
    exit(2)
}
var pages: [OCRPage] = []
for file in arguments.dropFirst() {
    guard let input = NSImage(contentsOfFile: file),
          let cg = input.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        fputs("Cannot read image: \(file)\n", stderr)
        exit(1)
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.003
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do {
        try handler.perform([request])
    } catch {
        fputs("OCR failed for \(file): \(error)\n", stderr)
        exit(1)
    }
    let lines = (request.results ?? []).compactMap { observation -> OCRLine? in
        let candidates = observation.topCandidates(3)
        guard let first = candidates.first else { return nil }
        let b = observation.boundingBox
        return OCRLine(text: first.string, confidence: first.confidence,
                       x: b.origin.x, y: 1 - b.origin.y - b.height,
                       width: b.width, height: b.height,
                       alternatives: candidates.dropFirst().map { $0.string })
    }.sorted { $0.y == $1.y ? $0.x < $1.x : $0.y < $1.y }
    pages.append(OCRPage(image: file, lines: lines))
    fputs("OCR \(URL(fileURLWithPath: file).lastPathComponent): \(lines.count) lines\n", stderr)
}
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
try encoder.encode(pages).write(to: URL(fileURLWithPath: arguments[0]))
