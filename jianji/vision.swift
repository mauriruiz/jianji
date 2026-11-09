// Apple Vision on a folder of frames, one JSON line per frame, in file-name order. jian compiles this once (swiftc)
// and caches it. Boxes are [x0, y0, x1, y1, confidence], normalised, origin top-left.
//   jian-vision <dir-of-jpgs>               faces and the salient subject
//   jian-vision <dir-of-jpgs> ocr|ocr-fast  text lines: [[text, x0, y0, x1, y1, confidence]] (es + en, no language
//                                           correction: code and file names stay as typed)
import Foundation
import Vision

let dir = URL(fileURLWithPath: CommandLine.arguments[1])
let mode = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "faces"
let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
    .filter { $0.pathExtension == "jpg" }.sorted { $0.lastPathComponent < $1.lastPathComponent }

func box(_ r: CGRect, _ c: Float) -> [Double] {
    [r.minX, 1 - r.maxY, r.maxX, 1 - r.minY, Double(c)].map { ($0 * 1000).rounded() / 1000 }
}

var out = [String](repeating: "", count: files.count)
let lock = NSLock()
DispatchQueue.concurrentPerform(iterations: files.count) { i in
    var result: [String: Any] = [:]
    if mode.hasPrefix("ocr") {
        let text = VNRecognizeTextRequest()
        text.recognitionLevel = mode == "ocr-fast" ? .fast : .accurate
        text.recognitionLanguages = ["es-ES", "en-US"]
        text.usesLanguageCorrection = false
        try? VNImageRequestHandler(url: files[i]).perform([text])
        result["text"] = (text.results ?? []).compactMap { o -> [Any]? in
            guard let c = o.topCandidates(1).first else { return nil }
            return [c.string] + box(o.boundingBox, c.confidence)
        }
    } else {
        let faces = VNDetectFaceRectanglesRequest()
        let objects = VNGenerateObjectnessBasedSaliencyImageRequest()
        try? VNImageRequestHandler(url: files[i]).perform([faces, objects])
        result["faces"] = (faces.results ?? []).map { box($0.boundingBox, $0.confidence) }
        result["salient"] = (objects.results?.first?.salientObjects ?? []).map { box($0.boundingBox, $0.confidence) }
    }
    let data = try! JSONSerialization.data(withJSONObject: result)
    lock.lock()
    out[i] = String(data: data, encoding: .utf8)!
    lock.unlock()
}
print(out.joined(separator: "\n"))
