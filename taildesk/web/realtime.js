(() => {
  "use strict";
  function create({ video, canvas, api, cursor, onStats, onFallback }) {
    let peer = null, channel = null, timer, epoch = 0, running = false, previous = null;
    function stop() {
      epoch += 1;
      running = false;
      clearInterval(timer);
      channel?.close();
      channel = null;
      const old = peer;
      peer = null;
      old?.close();
      video.pause();
      video.srcObject = null;
      video.classList.add("hidden");
      canvas.classList.remove("realtime-surface");
    }
    async function start() {
      stop();
      const id = epoch;
      if (!globalThis.RTCPeerConnection) return false;
      const pc = peer = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
      previous = null;
      const stream = new MediaStream();
      video.srcObject = stream;
      const v = pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
      const codecs = RTCRtpReceiver.getCapabilities("video")?.codecs.filter(c => c.mimeType.toLowerCase() === "video/h264");
      if (codecs?.length) v.setCodecPreferences(codecs);
      channel = pc.createDataChannel("control", { ordered: true });
      channel.onmessage = event => {
        if (epoch !== id) return;
        try {
          const data = JSON.parse(event.data);
          cursor.update(new Headers({ "X-TailDesk-Cursor": data.cursor, "X-TailDesk-Cursor-Stamp": data.stamp }));
        } catch (_) {}
      };
      pc.ontrack = event => {
        if (epoch !== id) return;
        stream.addTrack(event.track);
        // Browser hints, where implemented; do not accumulate a large playout buffer.
        if ("playoutDelayHint" in event.receiver) event.receiver.playoutDelayHint = 0;
        if ("jitterBufferTarget" in event.receiver) event.receiver.jitterBufferTarget = 0;
      };
      video.onresize = () => {
        if (video.videoWidth && epoch === id) {
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
        }
      };
      try {
        await pc.setLocalDescription(await pc.createOffer());
        if (pc.iceGatheringState !== "complete") await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error("ICE gathering timed out")), 5000);
          pc.onicegatheringstatechange = () => {
            if (pc.iceGatheringState === "complete") { clearTimeout(timeout); resolve(); }
          };
        });
        if (epoch !== id) return false;
        const answer = await api("/api/realtime", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(pc.localDescription), signal: AbortSignal.timeout(17000),
        });
        if (epoch !== id) return false;
        await pc.setRemoteDescription(answer);
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error("Video connection timed out")), 10000);
          const check = () => {
            if (epoch !== id || ["failed", "closed"].includes(pc.connectionState)) {
              clearTimeout(timeout); reject(new Error("Video connection ended"));
            } else if (pc.connectionState === "connected") { clearTimeout(timeout); resolve(); }
          };
          pc.onconnectionstatechange = check;
          check();
        });
        if (epoch !== id) return false;
        await video.play();
        running = true;
        video.classList.remove("hidden");
        canvas.classList.add("realtime-surface");
        pc.onconnectionstatechange = () => {
          if (epoch === id && ["failed", "closed", "disconnected"].includes(pc.connectionState)) {
            stop(); onFallback();
          }
        };
        let lastFrame = performance.now();
        timer = setInterval(async () => {
          if (epoch !== id) return;
          let reports;
          try { reports = await pc.getStats(); }
          catch (_) { if (epoch === id) { stop(); onFallback(); } return; }
          if (epoch !== id) return;
          let rtt = 0;
          reports.forEach(r => { if (r.type === "candidate-pair" && r.state === "succeeded") rtt = (r.currentRoundTripTime || 0) * 1000; });
          reports.forEach(r => {
            if (r.type !== "inbound-rtp" || r.kind !== "video") return;
            if (previous) {
              const seconds = (r.timestamp - previous.timestamp) / 1000;
              const frames = (r.framesDecoded || 0) - (previous.framesDecoded || 0);
              if (frames > 0) lastFrame = performance.now();
              onStats({ fps: frames / seconds, bitrate: (r.bytesReceived - previous.bytesReceived) * 8 / seconds,
                rtt, width: r.frameWidth, height: r.frameHeight,
                decodeMs: frames ? ((r.totalDecodeTime || 0) - (previous.totalDecodeTime || 0)) * 1000 / frames : 0 });
            }
            previous = r;
          });
          if (performance.now() - lastFrame > 5000 && epoch === id) { stop(); onFallback(); }
        }, 1000);
        return true;
      } catch (_) {
        if (epoch === id) stop();
        return false;
      }
    }
    function input(kind, values) {
      if (!running) return false;
      if (channel?.readyState !== "open") { stop(); onFallback(); return true; }
      if (channel.bufferedAmount > 65536) {
        if (kind === "move") return true;
        // Never silently lose key/button transitions or reorder them onto HTTP.
        stop(); onFallback();
        return true;
      }
      channel.send(JSON.stringify({ kind, ...values }));
      return true;
    }
    return { start, stop, input, get active() { return running; } };
  }
  globalThis.TailDeskRealtime = Object.freeze({ create });
})();
