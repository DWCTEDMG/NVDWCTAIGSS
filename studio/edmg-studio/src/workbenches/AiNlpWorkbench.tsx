import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Brain,
  CheckCircle2,
  Copy,
  Download,
  Film,
  Lock,
  LockOpen,
  Heart,
  LayoutGrid,
  Music,
  RefreshCcw,
  Sparkles,
  Upload,
  Wand2,
  Wrench,
  Zap,
} from 'lucide-react';
import { getBackendUrl } from '../components/api';
import { ProgressBar } from '../components/ProgressBar';
import { ErrorBoundary } from '../components/ErrorBoundary';

type AnalysisFocus = 'balanced' | 'emotion' | 'visual';
type PromptStyle = 'cinematic' | 'music-video' | 'experimental' | 'documentary';
type PromptDetail = 'tight' | 'standard' | 'expanded';
type AspectRatio = '16:9' | '9:16' | '1:1' | '21:9';
type PromptTarget = 'general-video' | 'runway' | 'deforum' | 'storyboard';

type EmotionResult = { emotion: string; confidence: number; intensity: string };
type ThemeResult = { theme: string; confidence: number };
type VisualImagery = { element: string; category: string; prominence: number };
type SpectralFeatures = {
  brightness: number;
  warmth: number;
  dynamicRange: number;
  zeroCrossingRate: number;
  averageEnergy: number;
  motionBias: number;
};
type SentimentSegment = {
  segment: number;
  startSeconds: number;
  endSeconds: number;
  sentiment: string;
  energy: number;
  energyLabel: string;
};
type AnalysisResult = {
  basicInfo: {
    fileName: string;
    duration: string;
    durationSeconds: number;
    tempo: number;
    key: string;
    sampleRate: number;
    channels: number;
  };
  emotions: EmotionResult[];
  themes: ThemeResult[];
  visualImagery: VisualImagery[];
  narrativeStructure: string;
  sentimentProgression: SentimentSegment[];
  spectralFeatures: SpectralFeatures;
  colorPalette: string[];
  motionProfile: string[];
  notes: string[];
  hookLine: string;
  energyCurve: number[];
};

type CreativeDirection = {
  concept: string;
  treatment: string;
  cameraLanguage: string[];
  lightingLanguage: string[];
  finishLanguage: string[];
  editLanguage: string[];
};

type PromptVariantMode = 'safe' | 'bold' | 'weird';
type PromptVariant = { mode: PromptVariantMode; text: string };
type SceneScore = { promptStrength: number; continuity: number; executionReadiness: number; overall: number };

type PromptScene = {
  id: number;
  title: string;
  segment: number;
  text: string;
  negativePrompt: string;
  rationale: string;
  shotType: string;
  transitionCue: string;
  continuityNote: string;
  approved: boolean;
  locked: boolean;
  status: 'draft' | 'approved' | 'needs-repair';
  score: SceneScore;
  variants: PromptVariant[];
};

type ScenePlanItem = {
  id: number;
  startTime: string;
  endTime: string;
  sectionLabel: string;
  shotType: string;
  movement: string;
  locationHint: string;
  transitionCue: string;
  continuityNote: string;
  approved: boolean;
};

type RerenderSuggestion = {
  id: number;
  sceneId: number;
  reason: string;
  promptAdjustment: string;
  executionNote: string;
};

type RepairPass = {
  id: number;
  sceneId: number;
  issue: string;
  fixStrategy: string;
};

type RenderManifest = {
  approvedSceneIds: number[];
  rerenderSceneIds: number[];
  repairSceneIds: number[];
  renderTargets: { target: PromptTarget; aspectRatio: AspectRatio; sceneId: number; seedHint: string; qualityPreset: string }[];
  modelHints: { baseFamily: string; recommendedPass: string; continuityPriority: string };
};

type OrchestrationPlan = {
  executiveSummary: string;
  direction: CreativeDirection;
  scenes: PromptScene[];
  scenePlan: ScenePlanItem[];
  keywordBank: string[];
  rerenderSuggestions: RerenderSuggestion[];
  repairPasses: RepairPass[];
  approvalChecklist: string[];
  renderManifest: RenderManifest;
};

type PlannerLabSyncPayload = {
  analysis: AnalysisResult;
  plan: OrchestrationPlan;
  settings: {
    analysisFocus: AnalysisFocus;
    promptStyle: PromptStyle;
    promptDetail: PromptDetail;
    aspectRatio: AspectRatio;
    target: PromptTarget;
    sceneCount: number;
    subjectFocus: string;
    creativeBrief: string;
    negativePromptSeed: string;
    selectedVariantMode: PromptVariantMode;
  };
};

type AiNlpWorkbenchProps = {
  studioProjectId?: string;
  studioProjectName?: string;
  studioProject?: any;
  studioSelectedVariant?: number;
  onSyncToStudio?: (payload: PlannerLabSyncPayload) => Promise<string | void>;
  compact?: boolean;
};

type PlannerWorkbenchSection = 'setup' | 'prompts' | 'storyboard' | 'repairs';

const AudioContextCtor: typeof AudioContext | undefined =
  typeof window !== 'undefined'
    ? (window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
    : undefined;

const STYLE_PRESETS: Record<PromptStyle, { camera: string[]; lighting: string[]; finish: string[]; edit: string[]; shotTypes: string[] }> = {
  cinematic: {
    camera: ['anamorphic lens language', 'patient dolly glide', 'crane reveal', 'measured close-up framing', 'lateral tracking sweep', 'foreground parallax pan'],
    lighting: ['soft rim light', 'motivated practicals', 'twilight contrast', 'volumetric haze'],
    finish: ['35mm texture', 'filmic contrast', 'premium color separation', 'shallow depth of field'],
    edit: ['long dissolves', 'measured rhythm', 'impact cut on musical lift', 'emotional crescendo'],
    shotTypes: ['wide establishing shot', 'hero medium shot', 'slow profile close-up', 'atmospheric insert', 'tracking side profile', 'foreground silhouette reveal'],
  },
  'music-video': {
    camera: ['kinetic handheld drift', 'flash-frame insert', 'snap zoom accent', 'performance-led orbit move', 'cross-frame tracking push', 'stage-width lateral whip'],
    lighting: ['pulse-synced practicals', 'neon spill', 'concert backlight', 'color-shifted haze'],
    finish: ['glossy editorial polish', 'high-energy contrast', 'stylized glow', 'dense color hits'],
    edit: ['beat-synced cuts', 'speed-ramped accents', 'performance and texture intercuts', 'section-based escalation'],
    shotTypes: ['performance close-up', 'choreography wide', 'tracking side profile', 'texture insert', 'crowd-through tracking shot', 'hero run-and-pan frame'],
  },
  experimental: {
    camera: ['rotational drift', 'macro abstraction', 'surreal push-in', 'fractured perspective', 'lateral smear pass', 'off-axis reveal'],
    lighting: ['spectral wash', 'overexposed edge light', 'color-separated shadows', 'strobing silhouettes'],
    finish: ['mixed-media texture', 'dream logic grade', 'painterly distortions', 'hallucinatory overlays'],
    edit: ['jump-cut discontinuity', 'memory-smear transitions', 'layered recursion', 'collapse and rebuild'],
    shotTypes: ['abstract macro', 'surreal tableau', 'collision of forms', 'impossible perspective', 'through-the-glass profile', 'split-depth insert'],
  },
  documentary: {
    camera: ['observational handheld frame', 'eye-level patience', 'walking follow shot', 'natural portrait coverage', 'measured side walk-by', 'quiet shoulder-level pan'],
    lighting: ['window-lit realism', 'practical ambient glow', 'overcast daylight', 'available light texture'],
    finish: ['grounded realism', 'honest grain', 'natural color science', 'minimal post stylization'],
    edit: ['chronological assembly', 'reaction inserts', 'location transitions', 'restraint before climax'],
    shotTypes: ['observational wide', 'intimate portrait', 'detail cutaway', 'walking follow shot', 'street-side profile', 'environmental reaction insert'],
  },
};

const VISUAL_BANK = {
  urban: ['city lights', 'wet asphalt reflections', 'subway platforms', 'rooftop silhouettes', 'mirrored towers', 'tunnel sodium vapor'],
  nature: ['mist over water', 'dust over fields', 'tree-line shadows', 'ocean horizon', 'wind through grass', 'cloud breaks'],
  movement: ['floating fabric', 'running figures', 'slow-motion dancers', 'suspended bodies', 'rotating light beams', 'arms cutting through smoke'],
  lighting: ['golden hour flare', 'neon underglow', 'volumetric haze', 'strobing backlight', 'headlights through smoke', 'projector bloom'],
  texture: ['film grain', 'rain on glass', 'dust in projector light', 'chromatic haze', 'mirrored fragments', 'lens bloom'],
} as const;

const THEME_BANK = [
  'liberation through movement',
  'memory versus momentum',
  'after-dark reinvention',
  'intimacy inside spectacle',
  'future nostalgia',
  'healing after rupture',
  'escape into motion',
  'self-discovery in public space',
];

const PALETTES = [
  ['electric cyan', 'deep magenta', 'sodium amber', 'midnight blue'],
  ['dusty gold', 'warm coral', 'weathered teal', 'soft charcoal'],
  ['silver fog', 'desaturated indigo', 'moonlit white', 'petrol green'],
  ['crimson pulse', 'violet haze', 'black chrome', 'cold white'],
];

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function average(values: number[]): number {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function percentile(values: number[], ratio: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * ratio)));
  return sorted[index];
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60)
    .toString()
    .padStart(2, '0');
  return `${mins}:${secs}`;
}

function formatClock(seconds: number): string {
  const mins = Math.floor(seconds / 60)
    .toString()
    .padStart(2, '0');
  const secs = Math.floor(seconds % 60)
    .toString()
    .padStart(2, '0');
  return `${mins}:${secs}`;
}

function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const textarea = document.createElement('textarea');
  textarea.value = text;
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
  return Promise.resolve();
}

function downloadText(filename: string, contents: string, type = 'text/plain'): void {
  const blob = new Blob([contents], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function humanizeEnergy(score: number): string {
  if (score > 0.82) return 'explosive';
  if (score > 0.64) return 'elevated';
  if (score > 0.42) return 'steady';
  return 'restrained';
}

function summarizeConfidence(score: number): string {
  if (score > 0.8) return 'very high';
  if (score > 0.62) return 'high';
  if (score > 0.42) return 'medium';
  return 'low';
}

function buildMonoChannel(buffer: AudioBuffer): Float32Array {
  const mono = new Float32Array(buffer.length);
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const data = buffer.getChannelData(channel);
    for (let index = 0; index < data.length; index += 1) mono[index] += data[index] / buffer.numberOfChannels;
  }
  return mono;
}

function estimateTempo(rmsFrames: number[], hopSeconds: number): number {
  if (rmsFrames.length < 8) return 96;
  const mean = average(rmsFrames);
  const threshold = mean * 1.16;
  const peaks: number[] = [];
  for (let i = 1; i < rmsFrames.length - 1; i += 1) {
    const current = rmsFrames[i];
    if (current > threshold && current >= rmsFrames[i - 1] && current > rmsFrames[i + 1]) {
      if (!peaks.length || i - peaks[peaks.length - 1] > 4) peaks.push(i);
    }
  }
  if (peaks.length < 2) return 96;
  const intervals: number[] = [];
  for (let i = 1; i < peaks.length; i += 1) intervals.push((peaks[i] - peaks[i - 1]) * hopSeconds);
  const medianSeconds = percentile(intervals, 0.5);
  if (!medianSeconds) return 96;
  let bpm = 60 / medianSeconds;
  while (bpm < 72) bpm *= 2;
  while (bpm > 168) bpm /= 2;
  return Math.round(bpm);
}

function estimateKey(samples: Float32Array, sampleRate: number): string {
  if (!samples.length) return 'C';
  const frameSize = Math.min(4096, samples.length);
  const start = Math.max(0, Math.floor(samples.length / 2) - Math.floor(frameSize / 2));
  const frame = samples.slice(start, start + frameSize);
  let bestLag = 0;
  let bestScore = -Infinity;
  const minLag = Math.floor(sampleRate / 880);
  const maxLag = Math.floor(sampleRate / 65);
  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let correlation = 0;
    for (let i = 0; i < frame.length - lag; i += 1) correlation += frame[i] * frame[i + lag];
    if (correlation > bestScore) {
      bestScore = correlation;
      bestLag = lag;
    }
  }
  if (!bestLag) return 'C';
  const frequency = sampleRate / bestLag;
  const noteNames = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const midi = Math.round(69 + 12 * Math.log2(frequency / 440));
  return noteNames[((midi % 12) + 12) % 12];
}

function deriveNarrativeStructure(segments: SentimentSegment[]): string {
  const energies = segments.map((segment) => segment.energy);
  const trend = energies[energies.length - 1] - energies[0];
  const peakIndex = energies.indexOf(Math.max(...energies));
  if (trend > 0.2 && peakIndex >= Math.floor(segments.length / 2)) return 'journey progression';
  if (peakIndex <= 1) return 'early burst';
  if (energies.every((value) => value < 0.45)) return 'meditative slow burn';
  return 'cyclical pulse';
}

function unique<T>(values: T[]): T[] {
  return Array.from(new Set(values));
}

function selectImagery(brightness: number, warmth: number, avgEnergy: number, focus: AnalysisFocus): VisualImagery[] {
  const categories: Array<keyof typeof VISUAL_BANK> = [];
  if (brightness > 0.58) categories.push('urban', 'lighting');
  if (warmth > 0.58) categories.push('nature', 'texture');
  if (avgEnergy > 0.45) categories.push('movement');
  if (focus === 'visual') categories.push('lighting', 'texture');
  if (focus === 'emotion') categories.push('nature', 'movement');
  if (!categories.length) categories.push('nature', 'lighting', 'texture');

  return unique(categories)
    .flatMap((category) =>
      VISUAL_BANK[category].map((element, index) => ({
        element,
        category,
        prominence: clamp01(0.55 + ((index + 1) / (VISUAL_BANK[category].length + 2)) * 0.35),
      }))
    )
    .slice(0, 6);
}

function deriveEmotions(avgEnergy: number, dynamicRange: number, warmth: number, brightness: number, focus: AnalysisFocus): EmotionResult[] {
  const candidates: EmotionResult[] = [
    { emotion: avgEnergy > 0.58 ? 'energy' : 'restraint', confidence: clamp01(0.62 + avgEnergy * 0.3), intensity: humanizeEnergy(avgEnergy) },
    { emotion: warmth > 0.58 ? 'nostalgia' : 'clarity', confidence: clamp01(0.52 + warmth * 0.28), intensity: warmth > 0.65 ? 'high' : 'medium' },
    { emotion: dynamicRange > 0.58 ? 'hope' : 'focus', confidence: clamp01(0.48 + dynamicRange * 0.34), intensity: dynamicRange > 0.72 ? 'high' : 'medium' },
    { emotion: brightness > 0.62 ? 'joy' : 'tension', confidence: clamp01(0.46 + Math.abs(brightness - 0.5) * 0.66), intensity: summarizeConfidence(brightness) },
  ];
  if (focus === 'visual') candidates.push({ emotion: 'wonder', confidence: 0.68, intensity: 'medium' });
  if (focus === 'emotion') candidates.push({ emotion: 'intimacy', confidence: 0.7, intensity: 'medium' });
  return candidates.slice(0, 5);
}

function selectThemes(emotions: EmotionResult[], focus: AnalysisFocus): ThemeResult[] {
  const names = emotions.map((emotion) => emotion.emotion);
  const themes: ThemeResult[] = [];
  if (names.includes('joy') && names.includes('energy')) themes.push({ theme: 'liberation through movement', confidence: 0.86 });
  if (names.includes('nostalgia')) themes.push({ theme: 'memory and return', confidence: 0.78 });
  if (names.includes('hope')) themes.push({ theme: 'personal breakthrough', confidence: 0.74 });
  if (names.includes('tension')) themes.push({ theme: 'pressure before release', confidence: 0.71 });
  if (focus === 'visual') themes.push({ theme: 'image-driven mood collage', confidence: 0.7 });
  if (focus === 'emotion') themes.push({ theme: 'interior emotional arc', confidence: 0.72 });
  if (!themes.length) themes.push(...THEME_BANK.slice(0, 3).map((theme, index) => ({ theme, confidence: 0.68 - index * 0.06 })));
  return themes.slice(0, 4);
}

async function analyzeAudioFile(file: File, focus: AnalysisFocus): Promise<AnalysisResult> {
  if (!AudioContextCtor) throw new Error('Web Audio API is not available in this browser.');
  const context = new AudioContextCtor();
  try {
    const buffer = await file.arrayBuffer();
    const audioBuffer = await context.decodeAudioData(buffer.slice(0));
    const mono = buildMonoChannel(audioBuffer);
    const frameSize = 2048;
    const hopSize = 1024;
    const rmsFrames: number[] = [];
    const diffFrames: number[] = [];
    const zeroCrossFrames: number[] = [];

    for (let start = 0; start + frameSize < mono.length; start += hopSize) {
      let squared = 0;
      let diffSum = 0;
      let zeroCrossings = 0;
      for (let i = start + 1; i < start + frameSize; i += 1) {
        const sample = mono[i];
        const prev = mono[i - 1];
        squared += sample * sample;
        diffSum += Math.abs(sample - prev);
        if ((sample >= 0 && prev < 0) || (sample < 0 && prev >= 0)) zeroCrossings += 1;
      }
      rmsFrames.push(Math.sqrt(squared / frameSize));
      diffFrames.push(diffSum / frameSize);
      zeroCrossFrames.push(zeroCrossings / frameSize);
    }

    const averageEnergy = clamp01(average(rmsFrames) * 4.4);
    const brightness = clamp01((average(diffFrames) / 0.09) * 0.68 + average(zeroCrossFrames) * 2.2);
    const dynamicRange = clamp01((percentile(rmsFrames, 0.9) - percentile(rmsFrames, 0.2)) * 5.8);
    const warmth = clamp01(1 - brightness * 0.55 + dynamicRange * 0.22 + averageEnergy * 0.15);
    const zeroCrossingRate = clamp01(average(zeroCrossFrames) * 5.2);
    const motionBias = clamp01(averageEnergy * 0.55 + brightness * 0.25 + dynamicRange * 0.2);
    const tempo = estimateTempo(rmsFrames, hopSize / audioBuffer.sampleRate);
    const key = estimateKey(mono, audioBuffer.sampleRate);
    const segmentCount = 8;
    const segmentDuration = audioBuffer.duration / segmentCount;
    const energyCurve = Array.from({ length: segmentCount }, (_, index) => {
      const start = Math.floor((index / segmentCount) * rmsFrames.length);
      const end = Math.max(start + 1, Math.floor(((index + 1) / segmentCount) * rmsFrames.length));
      return clamp01(average(rmsFrames.slice(start, end)) * 4.8);
    });

    const sentiments = energyCurve.map<SentimentSegment>((energy, index) => ({
      segment: index + 1,
      startSeconds: segmentDuration * index,
      endSeconds: segmentDuration * (index + 1),
      sentiment:
        energy > 0.82 ? 'euphoric' : energy > 0.64 ? 'driving' : energy > 0.42 ? 'building' : energy > 0.24 ? 'reflective' : 'suspended',
      energy,
      energyLabel: humanizeEnergy(energy),
    }));

    const emotions = deriveEmotions(averageEnergy, dynamicRange, warmth, brightness, focus);
    const themes = selectThemes(emotions, focus);
    const visualImagery = selectImagery(brightness, warmth, averageEnergy, focus);
    const palette = PALETTES[Math.round(clamp01((brightness + warmth) / 2) * (PALETTES.length - 1))] ?? PALETTES[0];
    const notes = [
      `${tempo} BPM suggests a ${tempo > 118 ? 'driving' : tempo > 96 ? 'steady' : 'restrained'} editorial rhythm.`,
      `${key} tonal center reads as ${warmth > 0.55 ? 'emotionally warm' : 'cool and precise'}.`,
      `${summarizeConfidence(dynamicRange)} dynamic range indicates ${dynamicRange > 0.62 ? 'clear lift sections' : 'consistent emotional pressure'}.`,
    ];
    const motionProfile = [
      motionBias > 0.62 ? 'camera movement can stay active' : 'camera movement should remain selective',
      brightness > 0.6 ? 'lean into highlights, flares, and reflective surfaces' : 'lean into silhouette and texture',
      warmth > 0.58 ? 'use lived-in palettes and tactile surfaces' : 'use steel, glass, and colder contrast',
    ];

    return {
      basicInfo: {
        fileName: file.name,
        duration: formatDuration(audioBuffer.duration),
        durationSeconds: audioBuffer.duration,
        tempo,
        key,
        sampleRate: audioBuffer.sampleRate,
        channels: audioBuffer.numberOfChannels,
      },
      emotions,
      themes,
      visualImagery,
      narrativeStructure: deriveNarrativeStructure(sentiments),
      sentimentProgression: sentiments,
      spectralFeatures: { brightness, warmth, dynamicRange, zeroCrossingRate, averageEnergy, motionBias },
      colorPalette: palette,
      motionProfile,
      notes,
      hookLine: `${themes[0]?.theme ?? 'emotional progression'} told through ${visualImagery[0]?.element ?? 'texture'} and ${visualImagery[1]?.element ?? 'movement'}`,
      energyCurve,
    };
  } finally {
    void context.close();
  }
}

function buildCreativeDirection(analysis: AnalysisResult, style: PromptStyle, subjectFocus: string, creativeBrief: string): CreativeDirection {
  const preset = STYLE_PRESETS[style];
  const emotionLabel = analysis.emotions.slice(0, 3).map((item) => item.emotion).join(', ');
  const concept = `${analysis.themes[0]?.theme ?? 'emotional transformation'} centered on ${subjectFocus || 'a charismatic lead subject'} inside a ${analysis.narrativeStructure} arc.`;
  const treatment = `${creativeBrief || 'Build a coherent visual progression that follows the emotional lift of the track.'} Use ${analysis.visualImagery
    .slice(0, 3)
    .map((item) => item.element)
    .join(', ')} to embody ${emotionLabel}.`;
  return {
    concept,
    treatment,
    cameraLanguage: preset.camera,
    lightingLanguage: preset.lighting,
    finishLanguage: preset.finish,
    editLanguage: preset.edit,
  };
}

function buildNegativePrompt(seed: string, target: PromptTarget): string {
  const base = ['muddy details', 'low contrast', 'unmotivated camera move', 'flat lighting', 'cheap-looking CG'];
  if (target === 'deforum') base.push('flicker', 'warped anatomy', 'temporal instability');
  if (target === 'runway') base.push('awkward body motion', 'stiff performance');
  if (seed.trim()) base.push(seed.trim());
  return base.join(', ');
}

function scoreScene(segment: SentimentSegment, detail: PromptDetail): SceneScore {
  const promptStrength = clamp01(0.45 + segment.energy * 0.4 + (detail === 'expanded' ? 0.1 : detail === 'standard' ? 0.05 : 0));
  const continuity = clamp01(0.55 + (1 - Math.abs(segment.energy - 0.58)) * 0.3);
  const executionReadiness = clamp01(0.5 + segment.energy * 0.22 + (detail === 'tight' ? 0.12 : 0));
  const overall = clamp01((promptStrength + continuity + executionReadiness) / 3);
  return { promptStrength, continuity, executionReadiness, overall };
}

function buildVariants(baseText: string): PromptVariant[] {
  return [
    { mode: 'safe', text: `${baseText}, preserve subject clarity, keep continuity conservative, reduce visual noise` },
    { mode: 'bold', text: `${baseText}, push contrast, increase motion intensity, stronger transition punctuation` },
    { mode: 'weird', text: `${baseText}, allow surreal texture collisions, dreamlike continuity leaks, stranger image logic` },
  ];
}

function choosePlannerShotType(preset: typeof STYLE_PRESETS[PromptStyle], index: number, energy: number): string {
  if (energy > 0.76) {
    const options = ['tracking side profile', 'hero medium tracking shot', 'cross-frame performance wide', 'foreground wipe reveal'];
    return options[index % options.length];
  }
  if (energy < 0.34) {
    const options = ['wide atmospheric hold', 'slow profile close-up', 'negative-space portrait', 'reflection-led insert'];
    return options[index % options.length];
  }
  return preset.shotTypes[index % preset.shotTypes.length];
}

function choosePlannerMovement(direction: CreativeDirection, index: number, energy: number): string {
  const base = direction.cameraLanguage[index % direction.cameraLanguage.length];
  const overlays =
    energy > 0.78
      ? ['subject crossing frame left-to-right', 'decisive lateral pan reset', 'faster parallax sweep through foreground elements']
      : energy > 0.56
      ? ['measured left-to-right travel', 'steady camera drift with environmental parallax', 'motivated subject movement through the frame']
      : ['soft side drift', 'quiet reframing around the subject', 'gentle pan reveal with stable axis'];
  return `${base}, ${overlays[index % overlays.length]}`;
}

function choosePlannerTransitionCue(index: number, total: number, energy: number): string {
  if (index === total - 1) return 'resolve into a held afterglow frame or a final drift-out with clean motion settle';
  if (energy > 0.78) return 'cut on impact, then reset the camera axis with a new lateral shove into the next beat';
  if (energy > 0.56) return 'bridge through motion continuity and let the subject or light source travel across frame into the next beat';
  return 'bleed through atmosphere, reflection, or a gentle pan continuation before the next section arrives';
}

function buildRenderManifest(planScenes: PromptScene[], target: PromptTarget, aspectRatio: AspectRatio): RenderManifest {
  const approvedSceneIds = planScenes.filter((scene) => scene.approved).map((scene) => scene.id);
  const rerenderSceneIds = planScenes.filter((scene) => scene.score.overall < 0.64 && !scene.approved).map((scene) => scene.id);
  const repairSceneIds = planScenes.filter((scene) => scene.status === 'needs-repair').map((scene) => scene.id);
  return {
    approvedSceneIds,
    rerenderSceneIds,
    repairSceneIds,
    renderTargets: planScenes.map((scene) => ({
      target,
      aspectRatio,
      sceneId: scene.id,
      seedHint: `scene-${scene.id}-${target}`,
      qualityPreset: scene.score.overall > 0.75 ? 'hero' : scene.score.overall > 0.58 ? 'standard' : 'repair',
    })),
    modelHints: {
      baseFamily: target === 'deforum' ? 'sdxl-motion' : target === 'runway' ? 'video-human-coherence' : 'general-cinematic-video',
      recommendedPass: approvedSceneIds.length ? 'render approved scenes first, then repair rejected scenes only' : 'run first-pass previews before committing hero renders',
      continuityPriority: 'hold subject silhouette, palette, and camera direction across adjacent sections',
    },
  };
}

function buildOrchestrationPlan(args: {
  analysis: AnalysisResult;
  style: PromptStyle;
  detail: PromptDetail;
  aspectRatio: AspectRatio;
  target: PromptTarget;
  sceneCount: number;
  subjectFocus: string;
  creativeBrief: string;
  negativePromptSeed: string;
}): OrchestrationPlan {
  const { analysis, style, detail, aspectRatio, target, sceneCount, subjectFocus, creativeBrief, negativePromptSeed } = args;
  const preset = STYLE_PRESETS[style];
  const direction = buildCreativeDirection(analysis, style, subjectFocus, creativeBrief);
  const negativePrompt = buildNegativePrompt(negativePromptSeed, target);
  const selectedSegments = analysis.sentimentProgression.slice(0, sceneCount);
  const detailText = detail === 'tight' ? 'keep the visual brief concise and production-ready' : detail === 'expanded' ? 'include strong environmental detail, lens behavior, performance language, and transition texture' : 'balance visual specificity with concise camera language';
  const platformHint =
    target === 'deforum'
      ? 'favor camera motion language and temporal continuity'
      : target === 'runway'
      ? 'favor coherent human movement and clear action verbs'
      : target === 'storyboard'
      ? 'favor shot design clarity and staging cues'
      : 'favor cinematic visual detail';
  const themeCycle = unique(analysis.themes.map((item) => item.theme).filter(Boolean));
  const paletteCycle = unique(analysis.colorPalette.filter(Boolean));
  const imageryPool = unique(
    analysis.visualImagery
      .map((item) => item.element)
      .filter(Boolean),
  );
  const finishCycle = unique(direction.finishLanguage.filter(Boolean));
  const editCycle = unique(direction.editLanguage.filter(Boolean));
  const phaseLabels = ['opening tableau', 'first lift', 'world expansion', 'pressure turn', 'release peak', 'resolution image'];

  const scenes: PromptScene[] = selectedSegments.map((segment, index) => {
    const imagery = Array.from({ length: Math.min(3, Math.max(1, imageryPool.length)) }, (_, offset) => {
      const pointer = (index * 2 + offset) % Math.max(1, imageryPool.length);
      return imageryPool[pointer];
    }).filter(Boolean);
    const shotType = choosePlannerShotType(preset, index, segment.energy);
    const movement = choosePlannerMovement(direction, index, segment.energy);
    const lighting = direction.lightingLanguage[index % direction.lightingLanguage.length];
    const finish = finishCycle[index % finishCycle.length] ?? preset.finish[0];
    const editLanguage = editCycle[index % editCycle.length] ?? preset.edit[0];
    const theme = themeCycle[index % Math.max(1, themeCycle.length)] ?? 'emotional lift';
    const paletteLead = paletteCycle[index % Math.max(1, paletteCycle.length)] ?? 'neutral steel';
    const paletteSupport = paletteCycle[(index + 1) % Math.max(1, paletteCycle.length)] ?? paletteLead;
    const phase = phaseLabels[Math.min(phaseLabels.length - 1, Math.floor((index / Math.max(1, selectedSegments.length - 1)) * (phaseLabels.length - 1)))];
    const compositionCue =
      segment.energy > 0.76
        ? ['foreground elements streaking by', 'light sources crossing behind the subject', 'camera axis resetting as the beat lands'][index % 3]
        : segment.energy > 0.5
        ? ['clear subject silhouette against moving texture', 'parallax depth through environmental layers', 'controlled movement from one edge of frame to the other'][index % 3]
        : ['negative space holding around the subject', 'slow environmental drift in the background', 'quiet reveal from shadow into the frame'][index % 3];
    const continuityNote =
      index === 0
        ? 'lock the lead subject, palette, and spatial world before introducing stronger motion changes'
        : `carry the lead silhouette and ${paletteLead} palette from scene ${index}, but change the camera lane, composition pressure, or environment texture so the section does not repeat`;
    const transitionCue = choosePlannerTransitionCue(index, selectedSegments.length, segment.energy);
    const hookReference =
      index === 0
        ? `Narrative anchor: ${analysis.hookLine}.`
        : index === selectedSegments.length - 1
        ? `Land the sequence back on ${analysis.hookLine}.`
        : '';
    const text = [
      `${shotType}, ${movement}, ${subjectFocus || 'magnetic lead performer'} staged as a ${phase} around ${imagery.join(', ') || 'a controlled visual world'}, with ${compositionCue}.`,
      `${lighting}, ${finish}, editing energy guided by ${editLanguage}, palette emphasis on ${paletteLead}${paletteSupport && paletteSupport !== paletteLead ? ` with ${paletteSupport} support` : ''}, theme focus ${theme}, and camera behavior that favors coherent lateral travel over repetitive zooming.`,
      `Segment ${segment.segment} plays as ${segment.sentiment} with ${segment.energyLabel} energy; ${detailText}; ${platformHint}; aspect ratio ${aspectRatio}.`,
      creativeBrief || 'Keep the sequence emotionally legible and visually escalating.',
      continuityNote,
      hookReference,
    ]
      .filter(Boolean)
      .join(' ');

    return {
      id: index + 1,
      title: `Scene ${index + 1}: ${segment.sentiment}`,
      segment: segment.segment,
      text,
      negativePrompt,
      rationale: `Uses ${imagery[0] ?? 'primary imagery'} to express ${segment.sentiment} while the ${movement} camera move and ${compositionCue} keep the section distinct from adjacent beats.`,
      shotType,
      transitionCue,
      continuityNote,
      approved: false,
      locked: false,
      status: 'draft',
      score: scoreScene(segment, detail),
      variants: buildVariants(text),
    };
  });

  const scenePlan: ScenePlanItem[] = scenes.map((scene, index) => ({
    id: scene.id,
    startTime: formatClock(selectedSegments[index].startSeconds),
    endTime: formatClock(selectedSegments[index].endSeconds),
    sectionLabel: selectedSegments[index].sentiment,
    shotType: scene.shotType,
    movement: choosePlannerMovement(direction, index, selectedSegments[index].energy),
    locationHint: imageryPool[index % Math.max(1, imageryPool.length)] ?? 'minimal stage space',
    transitionCue: scene.transitionCue,
    continuityNote: index === 0 ? 'lock character, palette, and environment for recall' : `carry forward ${paletteCycle[index % Math.max(1, paletteCycle.length)] ?? 'the lead palette'} and the subject silhouette`,
    approved: false,
  }));

  const rerenderSuggestions: RerenderSuggestion[] = scenes.map((scene) => ({
    id: scene.id,
    sceneId: scene.id,
    reason: scene.segment % 2 === 0 ? 'If motion feels too loose, tighten subject consistency and simplify camera changes.' : 'If the output lacks lift, increase energy cues and add brighter practicals.',
    promptAdjustment: scene.segment % 2 === 0 ? 'Reduce conflicting imagery, increase continuity, emphasize subject silhouette.' : 'Increase contrast, add forward motion, and strengthen transition accent.',
    executionNote: target === 'deforum' ? 'Keep temporal continuity stable; rerender with same seed family if possible.' : 'Preserve palette and pose continuity across rerenders.',
  }));

  const repairPasses: RepairPass[] = scenes.map((scene) => ({
    id: scene.id,
    sceneId: scene.id,
    issue: scene.segment % 3 === 0 ? 'Potential subject drift during higher-energy transitions.' : 'Potential texture inconsistency between sections.',
    fixStrategy: scene.segment % 3 === 0 ? 'Run a section repair pass with stronger subject lock, reduced background complexity, and consistent camera direction.' : 'Run a palette repair pass to reassert dominant colors, lens treatment, and texture stack.',
  }));

  const keywordBank = unique([
    ...analysis.colorPalette,
    ...analysis.themes.map((item) => item.theme),
    ...analysis.visualImagery.map((item) => item.element),
    ...direction.cameraLanguage.slice(0, 2),
    ...direction.lightingLanguage.slice(0, 2),
  ]).slice(0, 16);

  return {
    renderManifest: buildRenderManifest(scenes, target, aspectRatio),
    executiveSummary: `AI can operationally run the planning for this track: ${analysis.hookLine}. Keep human approval focused on taste, continuity, and final output selection.`,
    direction,
    scenes,
    scenePlan,
    keywordBank,
    rerenderSuggestions,
    repairPasses,
    approvalChecklist: [
      'Approve only scenes that preserve subject clarity and palette discipline.',
      'Reject scenes whose motion contradicts the song energy curve.',
      'Use rerender suggestions before manual rewriting when a scene is structurally correct but aesthetically weak.',
      'Use repair passes only on failed sections instead of rerendering the full sequence.',
    ],
  };
}

const STUDIO_COLOR_WORDS = [
  'moonlit white',
  'petrol green',
  'desaturated indigo',
  'silver fog',
  'amber',
  'crimson',
  'cobalt',
  'emerald',
  'violet',
  'teal',
  'blue',
  'green',
  'red',
  'gold',
  'orange',
  'white',
  'black',
];

function splitStudioPhrases(values: string[]): string[] {
  return unique(
    values
      .flatMap((value) => String(value || '').split(/[,.]| and /gi))
      .map((value) => value.trim())
      .filter((value) => value.length > 4),
  );
}

function extractStudioPalette(text: string, fallback: string[]): string[] {
  const lower = text.toLowerCase();
  const matches = STUDIO_COLOR_WORDS.filter((color) => lower.includes(color));
  return unique([...(matches.length ? matches : []), ...fallback]).slice(0, 4);
}

function buildStudioSentimentProgression(analysis: any, scenes: any[], durationSeconds: number): SentimentSegment[] {
  const sections = Array.isArray(analysis?.sections) ? analysis.sections : [];
  if (sections.length) {
    return sections.map((section: any, index: number) => {
      const energy = clamp01(Number(section?.energy ?? section?.avg_energy ?? 0.42));
      return {
        segment: index + 1,
        startSeconds: Number(section?.start_s ?? index * (durationSeconds / Math.max(1, sections.length))),
        endSeconds: Number(section?.end_s ?? (index + 1) * (durationSeconds / Math.max(1, sections.length))),
        sentiment: String(section?.label || section?.name || section?.energy_label || `section ${index + 1}`).toLowerCase(),
        energy,
        energyLabel: String(section?.energy_label || humanizeEnergy(energy)),
      };
    });
  }

  if (scenes.length) {
    return scenes.map((scene: any, index: number) => {
      const startSeconds = Number(scene?.start_s ?? index * 5);
      const endSeconds = Number(scene?.end_s ?? startSeconds + 5);
      const promptBlob = `${scene?.name || ''} ${scene?.prompt || ''}`.toLowerCase();
      const energy =
        clamp01(
          /burst|flash|impact|strobe|explosive|rush|surge/.test(promptBlob)
            ? 0.84
            : /drive|push|kinetic|chase|lift|glow/.test(promptBlob)
            ? 0.66
            : /drift|mist|reflect|slow|ambient|quiet/.test(promptBlob)
            ? 0.34
            : 0.5,
        );
      return {
        segment: index + 1,
        startSeconds,
        endSeconds,
        sentiment: String(scene?.name || `scene ${index + 1}`).toLowerCase(),
        energy,
        energyLabel: humanizeEnergy(energy),
      };
    });
  }

  const fallbackDuration = Math.max(12, durationSeconds || 48);
  return Array.from({ length: 6 }, (_, index) => {
    const startSeconds = (fallbackDuration / 6) * index;
    const endSeconds = (fallbackDuration / 6) * (index + 1);
    const energy = [0.28, 0.4, 0.52, 0.68, 0.8, 0.46][index] ?? 0.5;
    return {
      segment: index + 1,
      startSeconds,
      endSeconds,
      sentiment: energy > 0.72 ? 'peak' : energy > 0.54 ? 'lift' : energy < 0.38 ? 'reflective' : 'build',
      energy,
      energyLabel: humanizeEnergy(energy),
    };
  });
}

function synthesizePlannerAnalysisFromStudioProject(project: any, selectedVariant: number): AnalysisResult | null {
  const meta = project?.meta || {};
  const studioAnalysis = meta?.analysis || {};
  const studioPlan = meta?.last_plan || {};
  const variants = Array.isArray(studioPlan?.variants) ? studioPlan.variants : [];
  const variant = variants[selectedVariant] || variants[0] || {};
  const scenes = Array.isArray(variant?.scenes) ? variant.scenes : [];
  const features = studioAnalysis?.features || {};
  const durationSeconds = Number(features?.duration_s ?? features?.duration ?? meta?.audio?.duration_s ?? scenes[scenes.length - 1]?.end_s ?? 0);
  if (!durationSeconds && !scenes.length && !studioAnalysis) return null;

  const transcriptText = String(studioAnalysis?.transcript?.text || studioAnalysis?.summary || '').trim();
  const transcriptSentence = transcriptText.split(/(?<=[.!?])\s+/).find(Boolean) || transcriptText;
  const promptTexts = scenes.map((scene: any) => String(scene?.prompt || '')).filter(Boolean);
  const phraseCandidates = splitStudioPhrases([
    ...promptTexts,
    ...scenes.map((scene: any) => String(scene?.name || '')),
    ...((Array.isArray(studioAnalysis?.tags) ? studioAnalysis.tags : []).map(String)),
  ]);
  const visualImagery = phraseCandidates.slice(0, 8).map((element, index) => ({
    element,
    category: /city|street|tower|subway|rooftop|neon|tunnel/i.test(element)
      ? 'urban'
      : /ocean|water|mist|field|tree|grass|cloud/i.test(element)
      ? 'nature'
      : /glow|flare|shadow|light|haze/i.test(element)
      ? 'lighting'
      : /grain|dust|glass|smoke|texture/i.test(element)
      ? 'texture'
      : 'movement',
    prominence: clamp01(0.86 - index * 0.08),
  }));
  const averageEnergy = clamp01(Number(features?.energy ?? features?.average_energy ?? 0.48));
  const brightness = clamp01(Number(features?.brightness ?? features?.treble ?? 0.42));
  const warmth = clamp01(Number(features?.warmth ?? 1 - brightness * 0.45));
  const dynamicRange = clamp01(Number(features?.dynamic_range ?? features?.dynamicRange ?? 0.46));
  const zeroCrossingRate = clamp01(Number(features?.zero_crossing_rate ?? 0.22));
  const motionBias = clamp01(Number(features?.motion_bias ?? averageEnergy * 0.58 + brightness * 0.24 + dynamicRange * 0.18));
  const emotionsSource = Array.isArray(studioAnalysis?.emotions) ? studioAnalysis.emotions : [];
  const emotions = emotionsSource.length
    ? emotionsSource.slice(0, 5).map((emotion: any) => ({
        emotion: String(emotion?.emotion || emotion?.label || 'mood'),
        confidence: clamp01(Number(emotion?.score ?? emotion?.confidence ?? 0.62)),
        intensity: String(emotion?.intensity || summarizeConfidence(Number(emotion?.score ?? emotion?.confidence ?? 0.62))),
      }))
    : deriveEmotions(averageEnergy, dynamicRange, warmth, brightness, 'balanced');
  const themesSource = Array.isArray(studioAnalysis?.themes) ? studioAnalysis.themes : [];
  const themes = themesSource.length
    ? themesSource.slice(0, 5).map((theme: any, index: number) => ({
        theme: String(typeof theme === 'string' ? theme : theme?.theme || `theme ${index + 1}`),
        confidence: clamp01(Number(typeof theme === 'string' ? 0.72 - index * 0.06 : theme?.confidence ?? 0.72 - index * 0.06)),
      }))
    : selectThemes(emotions, 'balanced');
  const sentimentProgression = buildStudioSentimentProgression(studioAnalysis, scenes, durationSeconds || 48);
  const curveSource = Array.isArray(features?.energy_curve)
    ? features.energy_curve
    : Array.isArray(features?.energy)
    ? features.energy
    : Array.isArray(studioAnalysis?.waveform)
    ? studioAnalysis.waveform
    : sentimentProgression.map((segment) => segment.energy);
  const energyCurve = curveSource.map((value: any) => clamp01(Number(value ?? 0.5))).slice(0, 24);
  const fallbackPalette = PALETTES[Math.round(clamp01((brightness + warmth) / 2) * (PALETTES.length - 1))] ?? PALETTES[0];
  const colorPalette = extractStudioPalette(
    `${transcriptText} ${promptTexts.join(' ')} ${phraseCandidates.join(' ')}`,
    fallbackPalette,
  );

  return {
    basicInfo: {
      fileName: String(meta?.audio?.filename || 'project-audio'),
      duration: formatDuration(durationSeconds || 0),
      durationSeconds: durationSeconds || 0,
      tempo: Math.round(Number(features?.bpm ?? features?.tempo_bpm ?? features?.tempo ?? 96)),
      key: String(features?.key || 'C'),
      sampleRate: Math.round(Number(features?.sample_rate ?? 44100)),
      channels: Math.round(Number(features?.channels ?? 2)),
    },
    emotions,
    themes,
    visualImagery: visualImagery.length ? visualImagery : selectImagery(brightness, warmth, averageEnergy, 'balanced'),
    narrativeStructure: String(studioAnalysis?.narrative_structure || deriveNarrativeStructure(sentimentProgression)),
    sentimentProgression,
    spectralFeatures: { brightness, warmth, dynamicRange, zeroCrossingRate, averageEnergy, motionBias },
    colorPalette,
    motionProfile: unique([
      averageEnergy > 0.58 ? 'camera movement can stay active' : 'camera movement should remain selective',
      scenes.length ? `${scenes.length} saved storyboard scenes are already staged in the shared session` : '',
      brightness > 0.56 ? 'lean into highlights, reflections, and edge light' : 'lean into silhouette and atmosphere',
    ].filter(Boolean)),
    notes: unique([
      transcriptSentence || '',
      studioAnalysis?.summary || '',
      scenes.length ? `Hydrated from the saved Studio storyboard variant with ${scenes.length} scenes.` : '',
    ].filter(Boolean)),
    hookLine: transcriptSentence || promptTexts[0] || `${themes[0]?.theme ?? 'emotional lift'} told through ${visualImagery[0]?.element ?? 'texture'}`,
    energyCurve: energyCurve.length ? energyCurve : sentimentProgression.map((segment) => segment.energy),
  };
}

function buildPlannerPlanFromStudioProject(args: {
  project: any;
  selectedVariant: number;
  analysis: AnalysisResult;
  settings: PlannerLabSyncPayload['settings'];
}): OrchestrationPlan | null {
  const { project, selectedVariant, analysis, settings } = args;
  const canonicalPlan = project?.meta?.last_plan || {};
  const variants = Array.isArray(canonicalPlan?.variants) ? canonicalPlan.variants : [];
  const variant = variants[selectedVariant] || variants[0] || {};
  const canonicalScenes = Array.isArray(variant?.scenes) ? variant.scenes : [];
  const basePlannerPlan = project?.meta?.last_planner_lab?.plan;
  const canReusePlannerPlan =
    basePlannerPlan &&
    typeof basePlannerPlan === 'object' &&
    Array.isArray(basePlannerPlan?.scenes) &&
    Array.isArray(basePlannerPlan?.scenePlan) &&
    basePlannerPlan.scenes.length >= canonicalScenes.length &&
    canonicalScenes.length > 0;
  const seedPlan = canReusePlannerPlan
    ? (basePlannerPlan as OrchestrationPlan)
    : buildOrchestrationPlan({
        analysis,
        style: settings.promptStyle,
        detail: settings.promptDetail,
        aspectRatio: settings.aspectRatio,
        target: settings.target,
        sceneCount: canonicalScenes.length || settings.sceneCount,
        subjectFocus: settings.subjectFocus,
        creativeBrief: settings.creativeBrief,
        negativePromptSeed: settings.negativePromptSeed,
      });

  if (!canonicalScenes.length) return seedPlan;

  const studioScenes = canonicalScenes.map((scene: any, index: number) => {
    const baseScene = seedPlan.scenes[index] || seedPlan.scenes[seedPlan.scenes.length - 1];
    const matchingSegment =
      analysis.sentimentProgression[index] ||
      {
        segment: index + 1,
        startSeconds: Number(scene?.start_s ?? index * 5),
        endSeconds: Number(scene?.end_s ?? index * 5 + 5),
        sentiment: String(scene?.name || `scene ${index + 1}`).toLowerCase(),
        energy: clamp01((analysis.energyCurve[index] ?? 0.5) as number),
        energyLabel: humanizeEnergy((analysis.energyCurve[index] ?? 0.5) as number),
      };
    const promptText = String(scene?.prompt || baseScene?.text || '').trim() || 'Cinematic image sequence with a coherent subject and controlled atmosphere.';
    return {
      ...baseScene,
      id: index + 1,
      title: String(scene?.name || baseScene?.title || `Scene ${index + 1}`),
      segment: matchingSegment.segment,
      text: promptText,
      negativePrompt: String(scene?.negative_prompt || baseScene?.negativePrompt || buildNegativePrompt(settings.negativePromptSeed, settings.target)),
      rationale: baseScene?.rationale || `Carries the shared Studio creative direction into the planner for scene ${index + 1}.`,
      shotType: baseScene?.shotType || STYLE_PRESETS[settings.promptStyle].shotTypes[index % STYLE_PRESETS[settings.promptStyle].shotTypes.length],
      transitionCue: String(scene?.transition || baseScene?.transitionCue || 'bridge into the next beat with motion continuity'),
      continuityNote: baseScene?.continuityNote || (index === 0 ? 'establish the shared Studio visual world first' : `retain palette and subject continuity from scene ${index}`),
      approved: Boolean(baseScene?.approved),
      locked: Boolean(baseScene?.locked),
      status: baseScene?.status || 'draft',
      score: scoreScene(matchingSegment, settings.promptDetail),
      variants: buildVariants(promptText),
    } satisfies PromptScene;
  });

  return {
    ...seedPlan,
    scenes: studioScenes,
    scenePlan: canonicalScenes.map((scene: any, index: number) => ({
      id: index + 1,
      startTime: formatClock(Number(scene?.start_s ?? index * 5)),
      endTime: formatClock(Number(scene?.end_s ?? index * 5 + 5)),
      sectionLabel: analysis.sentimentProgression[index]?.sentiment || String(scene?.name || `scene ${index + 1}`),
      shotType: studioScenes[index]?.shotType || STYLE_PRESETS[settings.promptStyle].shotTypes[index % STYLE_PRESETS[settings.promptStyle].shotTypes.length],
      movement: seedPlan.scenePlan[index]?.movement || seedPlan.direction.cameraLanguage[index % seedPlan.direction.cameraLanguage.length],
      locationHint: analysis.visualImagery[index % Math.max(1, analysis.visualImagery.length)]?.element || 'shared project environment',
      transitionCue: studioScenes[index]?.transitionCue || seedPlan.scenePlan[index]?.transitionCue || 'bridge into the next beat',
      continuityNote: studioScenes[index]?.continuityNote || seedPlan.scenePlan[index]?.continuityNote || 'retain subject continuity',
      approved: studioScenes[index]?.approved || false,
    })),
    keywordBank: unique([
      ...seedPlan.keywordBank,
      ...analysis.colorPalette,
      ...analysis.themes.map((item) => item.theme),
      ...analysis.visualImagery.map((item) => item.element),
    ]).slice(0, 18),
    rerenderSuggestions: studioScenes.map((scene, index) => ({
      id: scene.id,
      sceneId: scene.id,
      reason: seedPlan.rerenderSuggestions[index]?.reason || 'Use a rerender only when the shared session prompt is structurally right but visually weak.',
      promptAdjustment: seedPlan.rerenderSuggestions[index]?.promptAdjustment || 'Tighten the prompt, simplify the background, and reinforce the subject silhouette.',
      executionNote: seedPlan.rerenderSuggestions[index]?.executionNote || 'Hold palette continuity and camera direction stable across adjacent sections.',
    })),
    repairPasses: studioScenes.map((scene, index) => ({
      id: scene.id,
      sceneId: scene.id,
      issue: seedPlan.repairPasses[index]?.issue || 'Potential continuity drift after manual storyboard changes.',
      fixStrategy: seedPlan.repairPasses[index]?.fixStrategy || 'Run a section repair pass with stronger subject lock and a simpler environmental stack.',
    })),
    renderManifest: buildRenderManifest(studioScenes, settings.target, settings.aspectRatio),
  };
}

async function fetchStudioAudioFile(projectId: string, fileName: string): Promise<File | null> {
  if (!projectId || !fileName) return null;
  const response = await fetch(`${getBackendUrl()}/v1/projects/${projectId}/audio`);
  if (!response.ok || typeof (response as Response & { blob?: () => Promise<Blob> }).blob !== 'function') return null;
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type || 'audio/*' });
}

const MetricBar: React.FC<{ label: string; value: number; accent: string }> = ({ label, value, accent }) => (
  <div>
    <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
      <span>{label}</span>
      <span>{Math.round(value * 100)}%</span>
    </div>
    <div className="h-2 rounded-full bg-slate-200">
      <div className={`h-2 rounded-full ${accent}`} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  </div>
);

const AIEnhancedMusicGenerator: React.FC<AiNlpWorkbenchProps> = ({
  studioProjectId,
  studioProjectName,
  studioProject,
  studioSelectedVariant = 0,
  onSyncToStudio,
  compact = false,
}) => {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const studioHydrationKeyRef = useRef('');
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [plan, setPlan] = useState<OrchestrationPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [analysisFocus, setAnalysisFocus] = useState<AnalysisFocus>('balanced');
  const [promptStyle, setPromptStyle] = useState<PromptStyle>('cinematic');
  const [promptDetail, setPromptDetail] = useState<PromptDetail>('standard');
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>('16:9');
  const [target, setTarget] = useState<PromptTarget>('general-video');
  const [sceneCount, setSceneCount] = useState(6);
  const [subjectFocus, setSubjectFocus] = useState('a magnetic central performer');
  const [creativeBrief, setCreativeBrief] = useState('Use emotionally legible visual storytelling with escalating momentum.');
  const [negativePromptSeed, setNegativePromptSeed] = useState('oversaturated skin, bad hands');
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [selectedVariantMode, setSelectedVariantMode] = useState<PromptVariantMode>('safe');
  const [studioSyncing, setStudioSyncing] = useState(false);
  const [studioSyncMessage, setStudioSyncMessage] = useState<string | null>(null);
  const [studioSyncError, setStudioSyncError] = useState<string | null>(null);
  const [studioSeedStatus, setStudioSeedStatus] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<PlannerWorkbenchSection>('setup');

  const approvedCount = useMemo(() => plan?.scenes.filter((scene) => scene.approved).length ?? 0, [plan]);
  const repairCount = useMemo(() => plan?.scenes.filter((scene) => scene.status === 'needs-repair').length ?? 0, [plan]);

  useEffect(() => {
    let cancelled = false;
    const studioAudioName = String(studioProject?.meta?.audio?.filename || '');
    const hydrationKey = [
      studioProjectId || '',
      studioSelectedVariant,
      studioAudioName,
      String(studioProject?.meta?.analysis?.timestamp || ''),
      String(studioProject?.meta?.last_planner_lab?.imported_at || ''),
      String(studioProject?.meta?.last_plan?.variants?.[studioSelectedVariant]?.scenes?.length || 0),
    ].join(':');
    if (!studioProjectId || !studioProject || hydrationKey === studioHydrationKeyRef.current) return;
    studioHydrationKeyRef.current = hydrationKey;

    const hydrate = async () => {
      const seedAnalysis =
        studioProject?.meta?.last_planner_lab?.analysis && typeof studioProject.meta.last_planner_lab.analysis === 'object'
          ? (studioProject.meta.last_planner_lab.analysis as AnalysisResult)
          : synthesizePlannerAnalysisFromStudioProject(studioProject, studioSelectedVariant);
      if (cancelled || !seedAnalysis) return;

      const savedPlannerSettings = studioProject?.meta?.last_planner_lab?.settings || {};
      const seedSettings = {
        analysisFocus: (savedPlannerSettings?.analysisFocus as AnalysisFocus) || analysisFocus,
        promptStyle: (savedPlannerSettings?.promptStyle as PromptStyle) || promptStyle,
        promptDetail: (savedPlannerSettings?.promptDetail as PromptDetail) || promptDetail,
        aspectRatio: (savedPlannerSettings?.aspectRatio as AspectRatio) || aspectRatio,
        target: (savedPlannerSettings?.target as PromptTarget) || target,
        sceneCount:
          Number(studioProject?.meta?.last_plan?.variants?.[studioSelectedVariant]?.scenes?.length || savedPlannerSettings?.sceneCount || sceneCount) || sceneCount,
        subjectFocus:
          String(savedPlannerSettings?.subjectFocus || subjectFocus || 'a magnetic central performer'),
        creativeBrief:
          String(
            savedPlannerSettings?.creativeBrief ||
              studioProject?.meta?.analysis?.summary ||
              creativeBrief ||
              'Use emotionally legible visual storytelling with escalating momentum.',
          ),
        negativePromptSeed:
          String(savedPlannerSettings?.negativePromptSeed || negativePromptSeed || 'oversaturated skin, bad hands'),
        selectedVariantMode:
          (savedPlannerSettings?.selectedVariantMode as PromptVariantMode) || selectedVariantMode,
      } satisfies PlannerLabSyncPayload['settings'];
      setAnalysisFocus(seedSettings.analysisFocus);
      setPromptStyle(seedSettings.promptStyle);
      setPromptDetail(seedSettings.promptDetail);
      setAspectRatio(seedSettings.aspectRatio);
      setTarget(seedSettings.target);
      setSceneCount(seedSettings.sceneCount);
      setSubjectFocus(seedSettings.subjectFocus);
      setCreativeBrief(seedSettings.creativeBrief);
      setNegativePromptSeed(seedSettings.negativePromptSeed);
      setSelectedVariantMode(seedSettings.selectedVariantMode);
      const seedPlan = buildPlannerPlanFromStudioProject({
        project: studioProject,
        selectedVariant: studioSelectedVariant,
        analysis: seedAnalysis,
        settings: seedSettings,
      });

      setAnalysis(seedAnalysis);
      if (seedPlan) setPlan(seedPlan);
      setActiveSection(seedPlan?.scenes?.length ? 'prompts' : 'setup');
      setStudioSeedStatus(
        `Hydrated from ${studioProjectName || 'the shared Studio project'}: audio, transcript, and storyboard data are already loaded here. Regenerate if you want a fresh planner pass.`,
      );

      if (studioAudioName && !cancelled) {
        try {
          const nextAudioFile = await fetchStudioAudioFile(studioProjectId, studioAudioName);
          if (!cancelled && nextAudioFile) setAudioFile(nextAudioFile);
        } catch {
          if (!cancelled) {
            setStudioSeedStatus(
              `Hydrated transcript and storyboard data from ${studioProjectName || 'the shared Studio project'}. Audio could not be reloaded automatically, so choose a local file if you want a new browser-side analysis pass.`,
            );
          }
        }
      }
    };

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [
    analysisFocus,
    creativeBrief,
    negativePromptSeed,
    promptDetail,
    promptStyle,
    sceneCount,
    selectedVariantMode,
    studioProject,
    studioProjectId,
    studioProjectName,
    studioSelectedVariant,
    subjectFocus,
    target,
    aspectRatio,
  ]);

  const buildStudioPayload = (): PlannerLabSyncPayload | null => {
    if (!analysis || !plan) return null;
    return {
      analysis,
      plan,
      settings: {
        analysisFocus,
        promptStyle,
        promptDetail,
        aspectRatio,
        target,
        sceneCount,
        subjectFocus,
        creativeBrief,
        negativePromptSeed,
        selectedVariantMode,
      },
    };
  };

  const runAnalysis = async (): Promise<void> => {
    if (!audioFile) return;
    setIsAnalyzing(true);
    setError(null);
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    try {
      const nextAnalysis = await analyzeAudioFile(audioFile, analysisFocus);
      const nextPlan = buildOrchestrationPlan({
        analysis: nextAnalysis,
        style: promptStyle,
        detail: promptDetail,
        aspectRatio,
        target,
        sceneCount,
        subjectFocus,
        creativeBrief,
        negativePromptSeed,
      });
      setAnalysis(nextAnalysis);
      setPlan(nextPlan);
      setActiveSection('prompts');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not analyze that file.');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const regeneratePlan = (): void => {
    if (!analysis) return;
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    const rebuiltPlan = buildOrchestrationPlan({
      analysis,
      style: promptStyle,
      detail: promptDetail,
      aspectRatio,
      target,
      sceneCount,
      subjectFocus,
      creativeBrief,
      negativePromptSeed,
    });
    setPlan((current) => {
      if (!current) return rebuiltPlan;
      const lockedScenes = new Map(current.scenes.filter((scene) => scene.locked).map((scene) => [scene.id, scene]));
      if (!lockedScenes.size) return rebuiltPlan;
      const nextScenes = rebuiltPlan.scenes.map((scene) => {
        const locked = lockedScenes.get(scene.id);
        return locked
          ? {
              ...locked,
              locked: true,
              variants: buildVariants(locked.text),
              score: locked.score,
            }
          : scene;
      });
      return {
        ...rebuiltPlan,
        scenes: nextScenes,
        scenePlan: rebuiltPlan.scenePlan.map((scene) => {
          const locked = lockedScenes.get(scene.id);
          return locked
            ? {
                ...scene,
                shotType: locked.shotType,
                transitionCue: locked.transitionCue,
                continuityNote: locked.continuityNote,
                approved: locked.approved,
              }
            : scene;
        }),
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
    setActiveSection('prompts');
  };

  const syncToStudio = async (): Promise<void> => {
    if (!onSyncToStudio) return;
    const payload = buildStudioPayload();
    if (!payload) return;
    setStudioSyncing(true);
    setStudioSyncMessage(null);
    setStudioSyncError(null);
    try {
      const result = await onSyncToStudio(payload);
      setActiveSection('storyboard');
      setStudioSyncMessage(
        typeof result === 'string' && result.trim()
          ? result
          : `Synced planner lab output into ${studioProjectName || 'the selected Studio project'} and applied it to the internal renderer timeline.`,
      );
    } catch (caught) {
      setStudioSyncError(caught instanceof Error ? caught.message : 'Could not sync the planner lab output into Studio.');
    } finally {
      setStudioSyncing(false);
    }
  };

  const toggleSceneApproval = (sceneId: number): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) => {
        if (scene.id !== sceneId) return scene;
        const nextStatus: PromptScene["status"] = !scene.approved
          ? "approved"
          : scene.status === "approved"
            ? "draft"
            : scene.status;
        return { ...scene, approved: !scene.approved, status: nextStatus };
      });
      return {
        ...current,
        scenes: nextScenes,
        scenePlan: current.scenePlan.map((scene) => (scene.id === sceneId ? { ...scene, approved: !scene.approved } : scene)),
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const approveAllScenes = (): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) => ({
        ...scene,
        approved: true,
        status: "approved",
      }));
      return {
        ...current,
        scenes: nextScenes,
        scenePlan: current.scenePlan.map((scene) => ({ ...scene, approved: true })),
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const clearApprovals = (): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) => ({
        ...scene,
        approved: false,
        status: scene.status === "approved" ? "draft" : scene.status,
      }));
      return {
        ...current,
        scenes: nextScenes,
        scenePlan: current.scenePlan.map((scene) => ({ ...scene, approved: false })),
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const toggleSceneLock = (sceneId: number): void => {
    setPlan((current) => current ? {
      ...current,
      scenes: current.scenes.map((scene) => (scene.id === sceneId ? { ...scene, locked: !scene.locked } : scene)),
    } : current);
  };

  const markSceneNeedsRepair = (sceneId: number): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) =>
        scene.id === sceneId ? { ...scene, approved: false, status: "needs-repair" } : scene
      );
      return {
        ...current,
        scenes: nextScenes,
        scenePlan: current.scenePlan.map((scene) => (scene.id === sceneId ? { ...scene, approved: false } : scene)),
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const applyRerenderSuggestion = (sceneId: number): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) =>
        scene.id === sceneId && !scene.locked
          ? {
              ...scene,
              text: `${scene.text}, rerender pass: reinforce palette discipline, increase subject consistency, simplify conflicting background cues`,
              status: "draft",
              approved: false,
              variants: buildVariants(
                `${scene.text}, rerender pass: reinforce palette discipline, increase subject consistency, simplify conflicting background cues`
              ),
            }
          : scene
      );
      return {
        ...current,
        scenes: nextScenes,
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const applyRepairPass = (sceneId: number): void => {
    setPlan((current) => {
      if (!current) return current;
      const nextScenes: PromptScene[] = current.scenes.map((scene) =>
        scene.id === sceneId && !scene.locked
          ? {
              ...scene,
              text: `${scene.text}, repair pass: lock subject identity, reassert dominant palette, reduce motion chaos, preserve lens continuity`,
              continuityNote: `${scene.continuityNote}; repair pass applied for continuity stabilization`,
              status: "draft",
              approved: false,
              variants: buildVariants(
                `${scene.text}, repair pass: lock subject identity, reassert dominant palette, reduce motion chaos, preserve lens continuity`
              ),
            }
          : scene
      );
      return {
        ...current,
        scenes: nextScenes,
        renderManifest: buildRenderManifest(nextScenes, target, aspectRatio),
      };
    });
  };

  const exportHandoffManifest = (): void => {
    if (!analysis || !plan) return;
    downloadText(
      `music-render-handoff-${Date.now()}.json`,
      JSON.stringify({
        createdAt: new Date().toISOString(),
        analysis: {
          fileName: analysis.basicInfo.fileName,
          durationSeconds: analysis.basicInfo.durationSeconds,
          tempo: analysis.basicInfo.tempo,
          key: analysis.basicInfo.key,
          hookLine: analysis.hookLine,
        },
        renderManifest: plan.renderManifest,
        approvedScenes: plan.scenes.filter((scene) => scene.approved).map((scene) => ({
          id: scene.id,
          title: scene.title,
          prompt: scene.text,
          negativePrompt: scene.negativePrompt,
          transitionCue: scene.transitionCue,
          continuityNote: scene.continuityNote,
          score: scene.score,
        })),
        repairQueue: plan.repairPasses.filter((item) => plan.scenes.some((scene) => scene.id === item.sceneId && scene.status === 'needs-repair')),
        rerenderQueue: plan.rerenderSuggestions.filter((item) => plan.renderManifest.rerenderSceneIds.includes(item.sceneId)),
      }, null, 2),
      'application/json'
    );
  };

  const copyPrompt = async (id: number, text: string): Promise<void> => {
    await copyText(text);
    setCopiedId(id);
    window.setTimeout(() => setCopiedId((current) => (current === id ? null : current)), 1200);
  };

  const copyApprovedPrompts = async (): Promise<void> => {
    if (!plan) return;
    const payload = plan.scenes
      .filter((scene) => scene.approved)
      .map((scene) => `${scene.title}\n${scene.text}\nNegative: ${scene.negativePrompt}`)
      .join('\n\n');
    await copyText(payload || 'No approved scenes yet.');
  };

  const exportJson = (): void => {
    if (!analysis || !plan) return;
    downloadText(
      `music-ai-director-pack-${Date.now()}.json`,
      JSON.stringify(
        {
          analysis,
          plan,
          settings: { analysisFocus, promptStyle, promptDetail, aspectRatio, target, sceneCount, subjectFocus, creativeBrief, negativePromptSeed },
          createdAt: new Date().toISOString(),
        },
        null,
        2
      ),
      'application/json'
    );
  };

  const exportMarkdown = (): void => {
    if (!analysis || !plan) return;
    const markdown = [
      '# AI-directed Music Video Workbook',
      '',
      `## Track`,
      `- File: ${analysis.basicInfo.fileName}`,
      `- Duration: ${analysis.basicInfo.duration}`,
      `- Tempo: ${analysis.basicInfo.tempo} BPM`,
      `- Key: ${analysis.basicInfo.key}`,
      '',
      `## Executive summary`,
      plan.executiveSummary,
      '',
      `## Creative direction`,
      plan.direction.concept,
      '',
      plan.direction.treatment,
      '',
      `## Prompts`,
      ...plan.scenes.flatMap((scene) => [
        `### ${scene.title}`,
        scene.text,
        `Negative: ${scene.negativePrompt}`,
        `Rationale: ${scene.rationale}`,
        `Transition cue: ${scene.transitionCue}`,
        `Continuity note: ${scene.continuityNote}`,
        `Approved: ${scene.approved ? 'yes' : 'no'}`,
        '',
      ]),
      `## Repair passes`,
      ...plan.repairPasses.map((item) => `- Scene ${item.sceneId}: ${item.issue} -> ${item.fixStrategy}`),
    ].join('\n');
    downloadText(`music-ai-director-pack-${Date.now()}.md`, markdown, 'text/markdown');
  };

  const exportSceneCsv = (): void => {
    if (!plan) return;
    const rows = [
      ['id', 'start_time', 'end_time', 'section', 'shot_type', 'movement', 'location_hint', 'transition_cue', 'continuity_note', 'approved'].join(','),
      ...plan.scenePlan.map((scene) =>
        [scene.id, scene.startTime, scene.endTime, scene.sectionLabel, scene.shotType, scene.movement, scene.locationHint, scene.transitionCue, scene.continuityNote, scene.approved ? 'yes' : 'no']
          .map((value) => `"${String(value).replace(/"/g, '""')}"`)
          .join(',')
      ),
    ].join('\n');
    downloadText(`music-scene-plan-${Date.now()}.csv`, rows, 'text/csv');
  };

  const sectionTabs: Array<{ id: PlannerWorkbenchSection; label: string; meta: string }> = [
    { id: 'setup', label: 'Setup', meta: audioFile ? 'audio loaded' : 'add track' },
    { id: 'prompts', label: 'Prompt Pack', meta: plan ? `${plan.scenes.length} scenes` : 'run planning' },
    { id: 'storyboard', label: 'Storyboard', meta: plan ? `${plan.scenePlan.length} beats` : 'plan first' },
    { id: 'repairs', label: 'Repairs', meta: plan ? `${plan.renderManifest.repairSceneIds.length} flagged` : 'idle' },
  ];

  return (
    <div className={`plannerLab-root ${compact ? 'plannerLab-root--compact' : ''} mx-auto max-w-7xl space-y-8 bg-slate-50 p-6 text-slate-900`}>
      <section className="rounded-3xl bg-white p-8 shadow-sm ring-1 ring-slate-200">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700">
              <Sparkles size={16} />
              AI-directed · tool-executed · human-supervised
            </div>
            <h1 className={`flex items-center gap-3 font-bold tracking-tight ${compact ? 'text-2xl' : 'text-4xl'}`}>
              <Music className="text-blue-600" />
              {compact ? 'AI Planner' : 'Music Video AI Director'}
            </h1>
            <p className="mt-3 max-w-3xl text-slate-600">
              This tool treats AI as the planner: it breaks down the song, generates the prompt pack, scene plan, rerender guidance, and repair passes — then you approve only what has taste.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button onClick={copyApprovedPrompts} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <Copy size={16} />
              Copy approved prompts
            </button>
            <button
              onClick={() => void syncToStudio()}
              disabled={!plan || !analysis || !onSyncToStudio || !studioProjectId || studioSyncing}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Upload size={16} />
              {studioSyncing ? 'Syncing renderer' : 'Sync to internal renderer'}
            </button>
            <button onClick={exportHandoffManifest} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <Download size={16} />
              Export handoff
            </button>
            <button onClick={exportJson} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
              <Download size={16} />
              Export JSON
            </button>
          </div>
        </div>
        <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-700">
          <div className="font-medium text-slate-900">Studio renderer bridge</div>
          <div className="mt-1">
            Target project: <strong>{studioProjectName || 'Select a project in the page bridge above'}</strong>
          </div>
          <div className="mt-1">
            Sync stores the full planner lab analysis and raw plan in project metadata, then derives canonical Studio analysis, `last_plan`, and prompt/motion timeline tracks for the internal renderer.
          </div>
          {studioSyncing ? (
            <div className="mt-3">
              <ProgressBar
                value={78}
                label="Planner handoff"
                detail="Writing planner metadata, variants, and timeline prompt tracks."
                compact
              />
            </div>
          ) : null}
          {studioSyncMessage && <div className="mt-3 rounded-xl bg-emerald-50 px-3 py-2 text-emerald-700">{studioSyncMessage}</div>}
          {studioSyncError && <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-rose-700">{studioSyncError}</div>}
        </div>
      </section>

      <details className="plannerLab-guide">
        <summary className="plannerLab-guideSummary">Quick guide and capabilities</summary>
        <div className="plannerLab-guideBody">
          <div className="guide-grid">
            <section className="guide-block">
              <div className="guide-kicker">What this tool does</div>
              <p>This planner turns a song and creative brief into a structured visual plan. It keeps setup, prompt writing, storyboard review, and repair strategy in separate tabs so you can focus without scrolling through the full stack every time.</p>
            </section>
            <section className="guide-block">
              <div className="guide-kicker">Capabilities</div>
              <ul className="guide-list">
                <li>Generate prompt packs tuned for cinematic, music-video, experimental, documentary, or storyboard output.</li>
                <li>Approve strong scenes, lock the beats you want to preserve from the shared storyboard, and regenerate the rest when you need alternates.</li>
                <li>Export or sync the planner output into the Studio renderer when you are satisfied with the plan.</li>
              </ul>
            </section>
            <section className="guide-block">
              <div className="guide-kicker">Recommended flow</div>
              <ul className="guide-list">
                <li>Start in Setup, load the track, and choose the analysis and prompt settings that match the target look.</li>
                <li>Move into Prompt Pack to refine scene language, use locks to keep the beats you like, then open Storyboard to check timing and reading order.</li>
                <li>Use Repairs for scenes that need recovery, then sync the plan when it is ready to become the saved Studio version.</li>
              </ul>
            </section>
          </div>
        </div>
      </details>

      <div className="plannerLab-tabs" role="tablist" aria-label="Planner sections">
        {sectionTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeSection === tab.id}
            className={`plannerLab-tab${activeSection === tab.id ? ' is-active' : ''}`}
            onClick={() => setActiveSection(tab.id)}
          >
            <span>{tab.label}</span>
            <span className="plannerLab-tabMeta">{tab.meta}</span>
          </button>
        ))}
      </div>

      {activeSection === 'setup' ? <section className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <div className="mb-4 flex items-center gap-2 text-lg font-semibold">
            <Upload className="text-blue-600" size={20} />
            Audio + orchestration controls
          </div>
          <div className="mb-6 cursor-pointer rounded-2xl border-2 border-dashed border-slate-300 p-8 text-center transition hover:border-blue-400 hover:bg-blue-50" onClick={() => fileInputRef.current?.click()}>
            <input
              ref={fileInputRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setAudioFile(file);
                setAnalysis(null);
                setPlan(null);
                setError(null);
                setStudioSeedStatus(file ? `Using local file ${file.name}. The shared Studio transcript and storyboard can still guide regeneration.` : null);
              }}
            />
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100">
              <Film className="text-slate-500" />
            </div>
            <div className="font-medium">{audioFile ? audioFile.name : 'Click to upload an audio file'}</div>
            <div className="mt-1 text-sm text-slate-500">MP3, WAV, M4A, AAC — fully local browser-side analysis</div>
          </div>
          {studioSeedStatus ? (
            <div className="mb-6 rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
              {studioSeedStatus}
            </div>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Analysis focus</span><select value={analysisFocus} onChange={(e) => setAnalysisFocus(e.target.value as AnalysisFocus)} className="w-full rounded-xl border border-slate-300 px-3 py-2"><option value="balanced">Balanced</option><option value="emotion">Emotion-led</option><option value="visual">Visual-led</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Prompt style</span><select value={promptStyle} onChange={(e) => setPromptStyle(e.target.value as PromptStyle)} className="w-full rounded-xl border border-slate-300 px-3 py-2"><option value="cinematic">Cinematic</option><option value="music-video">Music video</option><option value="experimental">Experimental</option><option value="documentary">Documentary</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Prompt detail</span><select value={promptDetail} onChange={(e) => setPromptDetail(e.target.value as PromptDetail)} className="w-full rounded-xl border border-slate-300 px-3 py-2"><option value="tight">Tight</option><option value="standard">Standard</option><option value="expanded">Expanded</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Aspect ratio</span><select value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value as AspectRatio)} className="w-full rounded-xl border border-slate-300 px-3 py-2"><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option><option value="21:9">21:9</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Output target</span><select value={target} onChange={(e) => setTarget(e.target.value as PromptTarget)} className="w-full rounded-xl border border-slate-300 px-3 py-2"><option value="general-video">General video</option><option value="runway">Runway-style</option><option value="deforum">Deforum-style</option><option value="storyboard">Storyboard</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Scene count</span><input type="number" min={4} max={16} value={sceneCount} onChange={(e) => setSceneCount(Math.max(4, Math.min(16, parseInt(e.target.value || '6', 10))))} className="w-full rounded-xl border border-slate-300 px-3 py-2" /></label>
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Subject focus</span><input value={subjectFocus} onChange={(e) => setSubjectFocus(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2" /></label>
            <label className="space-y-2 text-sm"><span className="font-medium text-slate-700">Negative prompt seed</span><input value={negativePromptSeed} onChange={(e) => setNegativePromptSeed(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2" /></label>
          </div>
          <label className="mt-4 block space-y-2 text-sm"><span className="font-medium text-slate-700">Creative brief</span><textarea value={creativeBrief} onChange={(e) => setCreativeBrief(e.target.value)} rows={3} className="w-full rounded-2xl border border-slate-300 px-3 py-2" /></label>

          <div className="mt-6 flex flex-wrap gap-3">
            <button onClick={runAnalysis} disabled={!audioFile || isAnalyzing} className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-3 font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
              {isAnalyzing ? <RefreshCcw className="animate-spin" size={18} /> : <Brain size={18} />} {isAnalyzing ? 'Analyzing and planning...' : 'Run AI planning pass'}
            </button>
            <button onClick={approveAllScenes} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <CheckCircle2 size={18} /> Approve all
            </button>
            <button onClick={clearApprovals} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <RefreshCcw size={18} /> Clear approvals
            </button>
            <button onClick={regeneratePlan} disabled={!analysis} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <Wand2 size={18} /> Regenerate plan
            </button>
            <button onClick={exportMarkdown} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <Download size={18} /> Export markdown brief
            </button>
            <button onClick={exportSceneCsv} disabled={!plan} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50">
              <LayoutGrid size={18} /> Export scene CSV
            </button>
          </div>
          {error ? <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div> : null}
        </div>

        <div className="space-y-6">
          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><Heart className="text-rose-500" size={20} />Analysis snapshot</div>
            {analysis ? (
              <div className="space-y-4 text-sm text-slate-700">
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-2xl bg-slate-50 p-3"><div className="text-slate-500">Tempo</div><div className="text-xl font-semibold">{analysis.basicInfo.tempo} BPM</div></div>
                  <div className="rounded-2xl bg-slate-50 p-3"><div className="text-slate-500">Key</div><div className="text-xl font-semibold">{analysis.basicInfo.key}</div></div>
                  <div className="rounded-2xl bg-slate-50 p-3"><div className="text-slate-500">Duration</div><div className="text-xl font-semibold">{analysis.basicInfo.duration}</div></div>
                  <div className="rounded-2xl bg-slate-50 p-3"><div className="text-slate-500">Narrative</div><div className="text-xl font-semibold capitalize">{analysis.narrativeStructure}</div></div>
                </div>
                <MetricBar label="Average energy" value={analysis.spectralFeatures.averageEnergy} accent="bg-blue-500" />
                <MetricBar label="Brightness" value={analysis.spectralFeatures.brightness} accent="bg-yellow-500" />
                <MetricBar label="Warmth" value={analysis.spectralFeatures.warmth} accent="bg-orange-500" />
                <MetricBar label="Dynamic range" value={analysis.spectralFeatures.dynamicRange} accent="bg-emerald-500" />
                <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-medium text-slate-800">Hook line</div><div>{analysis.hookLine}</div></div>
                <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-medium text-slate-800">Motion profile</div><ul className="list-inside list-disc text-slate-600">{analysis.motionProfile.map((item) => <li key={item}>{item}</li>)}</ul></div>
              </div>
            ) : <div className="rounded-2xl bg-slate-50 p-6 text-sm text-slate-500">Run a planning pass to populate energy, palette, imagery, and direction.</div>}
          </div>

          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><CheckCircle2 className="text-emerald-600" size={20} />Approval state</div>
            {plan ? (
              <div className="space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-3"><div className="rounded-2xl bg-slate-50 p-4 text-slate-700">Approved scenes: <span className="font-semibold">{approvedCount}</span> / {plan.scenes.length}</div><div className="rounded-2xl bg-slate-50 p-4 text-slate-700">Needs repair: <span className="font-semibold">{repairCount}</span></div></div>
                <div className="rounded-2xl bg-slate-50 p-4">
                  <div className="mb-2 font-medium text-slate-800">Approval checklist</div>
                  <ul className="list-inside list-disc text-slate-600">{plan.approvalChecklist.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              </div>
            ) : <div className="rounded-2xl bg-slate-50 p-6 text-sm text-slate-500">Approval state appears after the AI planning pass.</div>}
          </div>
        </div>
      </section> : null}

      {plan && activeSection === 'prompts' ? (
        <>
          <section className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><Zap className="text-amber-500" size={20} />Executive AI plan</div>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-semibold text-slate-800">Executive summary</div><p className="text-sm text-slate-700">{plan.executiveSummary}</p></div>
              <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-semibold text-slate-800">Keyword bank</div><div className="flex flex-wrap gap-2">{plan.keywordBank.map((item) => <span key={item} className="rounded-full border border-slate-200 px-3 py-1 text-xs">{item}</span>)}</div></div>
              <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-semibold text-slate-800">Concept</div><p className="text-sm text-slate-700">{plan.direction.concept}</p></div>
              <div className="rounded-2xl bg-slate-50 p-4"><div className="mb-2 font-semibold text-slate-800">Treatment</div><p className="text-sm text-slate-700">{plan.direction.treatment}</p></div>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-3"><div className="rounded-2xl bg-slate-50 p-4"><div className="text-sm font-medium text-slate-800">Approved for render</div><div className="mt-1 text-2xl font-semibold">{plan.renderManifest.approvedSceneIds.length}</div></div><div className="rounded-2xl bg-slate-50 p-4"><div className="text-sm font-medium text-slate-800">Queued rerenders</div><div className="mt-1 text-2xl font-semibold">{plan.renderManifest.rerenderSceneIds.length}</div></div><div className="rounded-2xl bg-slate-50 p-4"><div className="text-sm font-medium text-slate-800">Repair queue</div><div className="mt-1 text-2xl font-semibold">{plan.renderManifest.repairSceneIds.length}</div></div></div>
            <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-700"><strong>Model hints:</strong> {plan.renderManifest.modelHints.baseFamily} · {plan.renderManifest.modelHints.recommendedPass}</div>
          </section>

          <section className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-lg font-semibold"><Sparkles className="text-fuchsia-500" size={20} />Prompt pack</div><div className="flex items-center gap-2 text-sm"><span className="text-slate-500">Variant view</span><select value={selectedVariantMode} onChange={(e) => setSelectedVariantMode(e.target.value as PromptVariantMode)} className="rounded-xl border border-slate-300 px-3 py-2"><option value="safe">Safe</option><option value="bold">Bold</option><option value="weird">Weird</option></select></div></div>
            <div className="grid gap-4 lg:grid-cols-2">
              {plan.scenes.map((scene) => (
                <article key={scene.id} className="rounded-3xl border border-slate-200 p-5">
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs uppercase tracking-wide text-slate-500">{scene.shotType}</div>
                      <h3 className="text-lg font-semibold text-slate-900">{scene.title}</h3>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => void copyPrompt(scene.id, `${scene.text}\nNegative: ${scene.negativePrompt}`)} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700"><Copy size={14} />{copiedId === scene.id ? 'Copied' : 'Copy'}</button>
                      <button onClick={() => toggleSceneApproval(scene.id)} className={`inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium ${scene.approved ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-700'}`}><CheckCircle2 size={14} />{scene.approved ? 'Approved' : 'Approve'}</button><button onClick={() => toggleSceneLock(scene.id)} className="inline-flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700">{scene.locked ? <Lock size={14} /> : <LockOpen size={14} />}{scene.locked ? 'Locked' : 'Unlock'}</button>
                    </div>
                  </div>
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-xs"><span className={`rounded-full px-2 py-1 font-medium ${scene.status === 'approved' ? 'bg-emerald-100 text-emerald-700' : scene.status === 'needs-repair' ? 'bg-orange-100 text-orange-700' : 'bg-slate-100 text-slate-700'}`}>{scene.status}</span><span className="rounded-full bg-blue-50 px-2 py-1 font-medium text-blue-700">score {Math.round(scene.score.overall * 100)}%</span><span className="rounded-full bg-violet-50 px-2 py-1 font-medium text-violet-700">variant {selectedVariantMode}</span></div><div className="mb-3 text-sm leading-6 text-slate-700">{scene.text}</div>
                  <div className="mb-3 rounded-2xl bg-slate-50 p-3 text-sm text-slate-600"><strong>Negative:</strong> {scene.negativePrompt}</div><div className="mb-3 rounded-2xl bg-violet-50 p-3 text-sm text-violet-900"><strong>{selectedVariantMode} variant:</strong> {scene.variants.find((variant) => variant.mode === selectedVariantMode)?.text}</div>
                  <div className="mb-3 grid grid-cols-2 gap-2 text-xs text-slate-600 md:grid-cols-4"><div className="rounded-xl bg-slate-50 p-2"><div className="font-medium">Prompt</div>{Math.round(scene.score.promptStrength * 100)}%</div><div className="rounded-xl bg-slate-50 p-2"><div className="font-medium">Continuity</div>{Math.round(scene.score.continuity * 100)}%</div><div className="rounded-xl bg-slate-50 p-2"><div className="font-medium">Execution</div>{Math.round(scene.score.executionReadiness * 100)}%</div><div className="rounded-xl bg-slate-50 p-2"><div className="font-medium">Overall</div>{Math.round(scene.score.overall * 100)}%</div></div><div className="mb-3 flex flex-wrap gap-2"><button onClick={() => applyRerenderSuggestion(scene.id)} disabled={scene.locked} className="rounded-xl border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 disabled:opacity-50">Apply rerender note</button><button onClick={() => applyRepairPass(scene.id)} disabled={scene.locked} className="rounded-xl border border-orange-300 px-3 py-2 text-xs font-medium text-orange-700 disabled:opacity-50">Apply repair pass</button><button onClick={() => markSceneNeedsRepair(scene.id)} className="rounded-xl border border-orange-300 px-3 py-2 text-xs font-medium text-orange-700">Mark needs repair</button></div><div className="grid gap-3 text-sm md:grid-cols-2">
                    <div className="rounded-2xl bg-slate-50 p-3"><div className="font-medium text-slate-800">Rationale</div><div className="mt-1 text-slate-600">{scene.rationale}</div></div>
                    <div className="rounded-2xl bg-slate-50 p-3"><div className="font-medium text-slate-800">Transition cue</div><div className="mt-1 text-slate-600">{scene.transitionCue}</div></div>
                  </div>
                </article>
              ))}
            </div>
          </section>

        </>
      ) : null}

      {plan && activeSection === 'storyboard' ? (
        <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><LayoutGrid className="text-emerald-600" size={20} />Scene plan</div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead><tr className="text-left text-slate-500"><th className="px-3 py-3 font-medium">Time</th><th className="px-3 py-3 font-medium">Section</th><th className="px-3 py-3 font-medium">Shot</th><th className="px-3 py-3 font-medium">Transition</th><th className="px-3 py-3 font-medium">Approved</th></tr></thead>
                <tbody className="divide-y divide-slate-100">{plan.scenePlan.map((scene) => <tr key={scene.id}><td className="px-3 py-3 text-slate-600">{scene.startTime}–{scene.endTime}</td><td className="px-3 py-3 text-slate-700">{scene.sectionLabel}</td><td className="px-3 py-3 text-slate-700">{scene.shotType}</td><td className="px-3 py-3 text-slate-700">{scene.transitionCue}</td><td className="px-3 py-3 text-slate-700">{scene.approved ? 'yes' : 'no'}</td></tr>)}</tbody>
              </table>
            </div>
          </div>

          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><Film className="text-blue-600" size={20} />Storyboard reading order</div>
            <div className="space-y-3">
              {plan.scenes.map((scene) => (
                <div key={scene.id} className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-700">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-slate-900">{scene.title}</div>
                      <div className="text-xs uppercase tracking-wide text-slate-500">{scene.shotType}</div>
                    </div>
                    <div className="rounded-full bg-slate-200 px-2 py-1 text-xs">{scene.approved ? 'approved' : scene.status}</div>
                  </div>
                  <div className="mt-3">{scene.text}</div>
                  <div className="mt-3 text-slate-600"><strong>Transition:</strong> {scene.transitionCue}</div>
                </div>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {plan && activeSection === 'repairs' ? (
        <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><RefreshCcw className="text-violet-600" size={20} />Rerender suggestions</div>
            <div className="space-y-3">{plan.rerenderSuggestions.map((item) => <div key={item.id} className="rounded-2xl bg-slate-50 p-4 text-sm"><div className="font-medium text-slate-800">Scene {item.sceneId}</div><div className="mt-1 text-slate-600">{item.reason}</div><div className="mt-2 text-slate-700"><strong>Prompt adjustment:</strong> {item.promptAdjustment}</div><div className="mt-1 text-slate-700"><strong>Execution note:</strong> {item.executionNote}</div></div>)}</div>
          </div>
          <div className="rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <div className="mb-4 flex items-center gap-2 text-lg font-semibold"><Wrench className="text-orange-600" size={20} />Section repair passes</div>
            <div className="space-y-3">{plan.repairPasses.map((item) => <div key={item.id} className="rounded-2xl bg-slate-50 p-4 text-sm"><div className="font-medium text-slate-800">Scene {item.sceneId}</div><div className="mt-1 text-slate-600">{item.issue}</div><div className="mt-2 text-slate-700"><strong>Fix strategy:</strong> {item.fixStrategy}</div></div>)}</div>
          </div>
        </section>
      ) : null}
    </div>
  );
};

export default function AIEnhancedMusicGeneratorWithErrorBoundary(props: AiNlpWorkbenchProps) {
  return (
    <ErrorBoundary
      fallback={(error, retry) => (
        <div style={{ padding: '20px', textAlign: 'center' }}>
          <div style={{ color: '#d32f2f', marginBottom: '12px', fontWeight: 600 }}>
            Music Analysis Tool Error
          </div>
          <div style={{ color: '#666', marginBottom: '16px', fontSize: '14px' }}>
            {error.message}
          </div>
          <button
            onClick={retry}
            style={{
              padding: '8px 16px',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            Try Again
          </button>
        </div>
      )}
    >
      <AIEnhancedMusicGenerator {...props} />
    </ErrorBoundary>
  );
}
