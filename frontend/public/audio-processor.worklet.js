class MedJarvisAudioProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;
    }
    const channel = input[0];
    const copy = new Float32Array(channel.length);
    copy.set(channel);
    let sum = 0;
    for (let i = 0; i < copy.length; i += 1) {
      sum += copy[i] * copy[i];
    }
    const rms = Math.sqrt(sum / Math.max(1, copy.length));
    this.port.postMessage(
      {
        type: "audio",
        sampleRate,
        rms,
        samples: copy
      },
      [copy.buffer]
    );
    return true;
  }
}

registerProcessor("medjarvis-audio-processor", MedJarvisAudioProcessor);

