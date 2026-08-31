import Foundation

struct AudioTranscriptionResponse {
    let transcript: String
    let language: String?

    var text: String {
        transcript
    }

    init(transcript: String, language: String?) {
        self.transcript = transcript
        self.language = language
    }

    init(api response: APIAudioTranscriptionResponse) {
        transcript = response.transcript
        language = response.language
    }
}
