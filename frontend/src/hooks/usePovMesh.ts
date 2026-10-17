/** usePovMesh: WebRTC camera sharing (full mesh via WS signaling).
 * Each peer publishes an optional camera stream; signaling (offer/answer/ice)
 * is relayed by the backend WS without ever touching media bytes.
 */
import { useEffect, useRef, useState } from 'react';

type SignalMsg = { from: string; kind: 'offer' | 'answer' | 'ice'; payload: unknown };
type NegotiationState = {
  makingOffer: boolean;
  ignoreOffer: boolean;
  settingRemoteAnswer: boolean;
  polite: boolean;
};

export function usePovMesh(params: {
  peers: { participant_id: string }[];
  selfId: string;
  sendSignal: (to: string, kind: string, payload: unknown) => void;
}) {
  const { peers, selfId, sendSignal } = params;
  const [localStream, setLocalStream] = useState<MediaStream | null>(null);
  const [remoteStreams, setRemoteStreams] = useState<Record<string, MediaStream>>({});
  const pcs = useRef<Record<string, RTCPeerConnection>>({});
  const negotiationStates = useRef<Record<string, NegotiationState>>({});
  const pendingCandidates = useRef<Record<string, RTCIceCandidateInit[]>>({});
  const streamRef = useRef<MediaStream | null>(null);
  const sendSignalRef = useRef(sendSignal);
  sendSignalRef.current = sendSignal;

  const attachTracks = (peerId: string, pc: RTCPeerConnection, stream: MediaStream) => {
    stream.getTracks().forEach((track) => {
      const senders = pc.getSenders();
      if (senders.some((sender) => sender.track?.id === track.id)) return;

      const reusable = pc.getTransceivers().find(
        (transceiver) => !transceiver.sender.track && transceiver.receiver.track.kind === track.kind,
      );
      if (reusable) {
        void reusable.sender.replaceTrack(track).then(() => {
          if (reusable.direction === 'recvonly') reusable.direction = 'sendrecv';
        }).catch((error: unknown) => {
          console.error(`Could not attach camera track for participant ${peerId}`, error);
        });
      } else {
        pc.addTrack(track, stream);
      }
    });
  };

  const ensurePc = (peerId: string) => {
    if (pcs.current[peerId]) return pcs.current[peerId];
    const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
    const stream = streamRef.current;
    const negotiation: NegotiationState = {
      makingOffer: false,
      ignoreOffer: false,
      settingRemoteAnswer: false,
      polite: selfId > peerId,
    };
    negotiationStates.current[peerId] = negotiation;
    if (stream) attachTracks(peerId, pc, stream);
    pc.onicecandidate = (e) => {
      if (e.candidate) sendSignalRef.current(peerId, 'ice', e.candidate.toJSON());
    };
    pc.ontrack = (e) => {
      const [stream] = e.streams;
      if (stream) setRemoteStreams((s) => ({ ...s, [peerId]: stream }));
    };
    pc.onnegotiationneeded = async () => {
      try {
        negotiation.makingOffer = true;
        await pc.setLocalDescription();
        if (pc.localDescription) {
          sendSignalRef.current(peerId, 'offer', pc.localDescription);
        }
      } catch (error) {
        console.error(`Could not negotiate camera with participant ${peerId}`, error);
      } finally {
        negotiation.makingOffer = false;
      }
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        setRemoteStreams((streams) => {
          const { [peerId]: _removed, ...remaining } = streams;
          return remaining;
        });
      }
    };
    pcs.current[peerId] = pc;
    return pc;
  };

  // Create peer connections from the full roster, including the initial room
  // snapshot delivered during the WebSocket handshake.
  useEffect(() => {
    const peerIds = new Set(
      peers.map((peer) => peer.participant_id).filter((id) => id && id !== selfId),
    );
    peerIds.forEach((peerId) => ensurePc(peerId));
    Object.entries(pcs.current).forEach(([peerId, pc]) => {
      if (peerIds.has(peerId)) return;
      pc.close();
      delete pcs.current[peerId];
      delete negotiationStates.current[peerId];
      delete pendingCandidates.current[peerId];
      setRemoteStreams((streams) => {
        const { [peerId]: _removed, ...remaining } = streams;
        return remaining;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peers, selfId]);

  // Add newly enabled camera tracks to existing connections. The negotiation
  // handler above renegotiates those connections.
  useEffect(() => {
    Object.entries(pcs.current).forEach(([peerId, pc]) => {
      if (localStream) attachTracks(peerId, pc, localStream);
    });
    streamRef.current = localStream;
  }, [localStream]);

  const onSignal = async (msg: SignalMsg) => {
    const pc = ensurePc(msg.from);
    const negotiation = negotiationStates.current[msg.from];
    if (msg.kind === 'offer') {
      const readyForOffer = !negotiation.makingOffer
        && (pc.signalingState === 'stable' || negotiation.settingRemoteAnswer);
      const offerCollision = !readyForOffer;
      negotiation.ignoreOffer = !negotiation.polite && offerCollision;
      if (negotiation.ignoreOffer) return;
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      for (const candidate of pendingCandidates.current[msg.from] || []) {
        await pc.addIceCandidate(new RTCIceCandidate(candidate));
      }
      delete pendingCandidates.current[msg.from];
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      if (pc.localDescription) sendSignalRef.current(msg.from, 'answer', pc.localDescription);
    } else if (msg.kind === 'answer') {
      negotiation.settingRemoteAnswer = true;
      try {
        await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      } finally {
        negotiation.settingRemoteAnswer = false;
      }
      for (const candidate of pendingCandidates.current[msg.from] || []) {
        await pc.addIceCandidate(new RTCIceCandidate(candidate));
      }
      delete pendingCandidates.current[msg.from];
    } else if (msg.kind === 'ice') {
      const candidate = msg.payload as RTCIceCandidateInit;
      if (negotiation.ignoreOffer) return;
      if (pc.remoteDescription) {
        await pc.addIceCandidate(new RTCIceCandidate(candidate));
      } else {
        pendingCandidates.current[msg.from] = [
          ...(pendingCandidates.current[msg.from] || []),
          candidate,
        ];
      }
    }
  };

  const toggleCamera = async (on: boolean) => {
    if (on) {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      streamRef.current = stream;
      setLocalStream(stream);
      return stream;
    }
    const stream = streamRef.current;
    if (stream) {
      await Promise.all(
        Object.values(pcs.current).flatMap((pc) =>
          pc.getSenders()
            .filter((sender) => stream.getTracks().some((track) => track.id === sender.track?.id))
            .map((sender) => sender.replaceTrack(null)),
        ),
      );
      stream.getTracks().forEach((track) => track.stop());
    }
    streamRef.current = null;
    setLocalStream(null);
    return null;
  };

  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    Object.values(pcs.current).forEach((pc) => pc.close());
  }, []);

  return { localStream, remoteStreams, onSignal, toggleCamera };
}
