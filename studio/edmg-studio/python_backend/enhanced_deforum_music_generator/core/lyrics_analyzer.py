"""
Lyrics Analysis Module
Handles transcription and emotional analysis of song lyrics
"""

import re
import logging
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass
from collections import Counter

# Configure logging
logger = logging.getLogger(__name__)

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logger.info("Whisper not available - lyric analysis disabled")

@dataclass
class LyricAnalysis:
    """Structured lyric analysis results"""
    has_lyrics: bool
    raw_text: str
    emotions: List[str]
    emotion_details: Dict[str, Dict[str, Any]]
    themes: List[str]
    visual_elements: List[str]
    narrative_structure: Dict[str, Any]
    emotional_intensity: float
    deforum_prompts: List[str]
    word_count: int

class EmotionClassifier:
    """Emotion detection from lyrical content"""
    
    EMOTION_KEYWORDS = {
        "joy": {
            "keywords": ["happy", "joy", "smile", "laugh", "celebrate", "dance", 
                        "bright", "shine", "amazing", "wonderful", "love", "beautiful"],
            "weight_words": ["ecstatic", "blissful", "euphoric", "radiant"],
            "visual_style": "bright colors, warm lighting, uplifting composition, golden hour",
            "camera_hint": "upward movement, open framing"
        },
        "love": {
            "keywords": ["love", "heart", "kiss", "together", "forever", "romance", 
                        "dear", "soul", "embrace", "tender", "passion"],
            "weight_words": ["devotion", "adoration", "cherish", "beloved"],
            "visual_style": "romantic lighting, warm tones, soft focus, intimate framing",
            "camera_hint": "gentle zoom, stable framing"
        },
        "energy": {
            "keywords": ["energy", "power", "strong", "fire", "electric", "alive", 
                        "wild", "force", "intense", "explosive", "rock", "metal"],
            "weight_words": ["thunderous", "volcanic", "lightning", "unstoppable"],
            "visual_style": "dynamic lighting, high contrast, motion blur, energetic composition",
            "camera_hint": "dynamic movement, quick transitions"
        },
        "peace": {
            "keywords": ["peace", "calm", "quiet", "gentle", "serene", "still", 
                        "meditation", "tranquil", "soft", "whisper", "zen"],
            "weight_words": ["blissful", "harmonious", "placid", "ethereal"],
            "visual_style": "soft lighting, pastel colors, gentle transitions, tranquil composition",
            "camera_hint": "slow movement, stable shots"
        },
        "melancholy": {
            "keywords": ["sad", "lonely", "lost", "empty", "tears", "rain", "darkness", 
                        "shadow", "broken", "pain", "sorrow", "blue"],
            "weight_words": ["anguish", "despair", "forlorn", "wistful"],
            "visual_style": "muted colors, soft shadows, melancholic atmosphere, desaturated tones",
            "camera_hint": "slow zoom out, downward drift"
        },
        "mystery": {
            "keywords": ["mystery", "hidden", "secret", "shadow", "night", "unknown", 
                        "whisper", "darkness", "enigma", "puzzle", "strange"],
            "weight_words": ["cryptic", "arcane", "elusive", "otherworldly"],
            "visual_style": "low key lighting, atmospheric shadows, mysterious mood",
            "camera_hint": "subtle movement, revealing shots"
        }
    }
    
    def __init__(self):
        # Pre-compile regex patterns for efficiency
        self.word_patterns = {}
        for emotion, data in self.EMOTION_KEYWORDS.items():
            all_words = data["keywords"] + data.get("weight_words", [])
            pattern = r'\b(' + '|'.join(re.escape(word) for word in all_words) + r')\b'
            self.word_patterns[emotion] = re.compile(pattern, re.IGNORECASE)
    
    def classify_emotions(self, text: str) -> Dict[str, Dict]:
        """Classify emotions with confidence scoring"""
        if not text or len(text.strip()) < 10:
            return {}
        
        text_lower = text.lower()
        emotion_scores = {}
        
        for emotion, pattern in self.word_patterns.items():
            matches = pattern.findall(text_lower)
            if matches:
                data = self.EMOTION_KEYWORDS[emotion]
                
                # Calculate confidence
                unique_matches = set(matches)
                keyword_count = len(data["keywords"])
                weight_matches = sum(1 for match in unique_matches 
                                   if match in data.get("weight_words", []))
                
                # Weighted confidence calculation
                base_confidence = len(unique_matches) / keyword_count
                weight_boost = weight_matches * 0.2
                final_confidence = min(1.0, base_confidence + weight_boost)
                
                if final_confidence > 0.1:  # Minimum threshold
                    emotion_scores[emotion] = {
                        "confidence": final_confidence,
                        "matches": list(unique_matches),
                        "match_count": len(matches),
                        "visual_style": data["visual_style"],
                        "camera_hint": data["camera_hint"]
                    }
        
        return emotion_scores

class ThemeExtractor:
    """Extract thematic elements from lyrics"""
    
    COMMON_WORDS = {
        "that", "with", "have", "this", "will", "your", "from", "they", 
        "know", "want", "been", "good", "much", "some", "time", "very", 
        "when", "come", "here", "just", "like", "over", "also", "back", 
        "after", "first", "well", "year", "work", "such", "make", "even",
        "could", "should", "would", "there", "where", "their", "these",
        "those", "what", "which", "said", "each", "than", "more", "most"
    }
    
    VISUAL_KEYWORDS = {
        "nature": ["sun", "moon", "star", "sky", "cloud", "ocean", "sea", "mountain", 
                  "forest", "river", "tree", "flower", "garden", "wind", "storm"],
        "urban": ["city", "street", "building", "car", "plane", "train", "bridge", 
                 "tower", "house", "window", "door"],
        "colors": ["red", "blue", "green", "yellow", "black", "white", "purple", 
                  "orange", "pink", "gold", "silver"],
        "emotions_visual": ["eyes", "smile", "face", "hands", "tears", "kiss"],
        "actions": ["dance", "run", "fly", "walk", "jump", "fall", "rise", "move"],
        "abstract": ["dream", "hope", "memory", "soul", "spirit", "mind", "heart"]
    }
    
    def extract_themes(self, text: str) -> List[str]:
        """Extract meaningful thematic words"""
        if not text:
            return []
        
        # Clean and tokenize
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        
        # Filter common words
        meaningful_words = [word for word in words 
                          if word not in self.COMMON_WORDS]
        
        # Get most frequent themes
        theme_counts = Counter(meaningful_words)
        return [theme for theme, count in theme_counts.most_common(10)]
    
    def extract_visual_elements(self, text: str) -> List[str]:
        """Extract visual elements for animation"""
        if not text:
            return []
        
        text_lower = text.lower()
        found_elements = []
        
        for category, keywords in self.VISUAL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    found_elements.append(keyword)
        
        return list(set(found_elements))  # Remove duplicates

class NarrativeAnalyzer:
    """Analyze narrative structure and progression"""
    
    TEMPORAL_INDICATORS = {
        "past": ["was", "were", "had", "did", "used to", "remember", 
                "yesterday", "before", "once", "ago"],
        "present": ["am", "is", "are", "now", "today", "currently", 
                   "right now", "at this moment"],
        "future": ["will", "going to", "tomorrow", "future", "someday", 
                  "next", "soon", "eventually"]
    }
    
    def analyze_structure(self, text: str) -> Dict[str, Any]:
        """Analyze narrative structure"""
        if not text:
            return self._get_default_structure()

        # Basic structure analysis
        sentences = self._split_sentences(text)
        
        structure = {
            "sentence_count": len(sentences),
            "has_questions": "?" in text,
            "has_exclamations": "!" in text,
            "repetitive_structure": self._detect_repetition(sentences),
            "story_progression": self._detect_progression(text),
            "verse_chorus_pattern": self._detect_verse_chorus(text)
        }
        
        return structure
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        # Replace multiple punctuation and split
        text = re.sub(r'[!?]+', '.', text)
        sentences = [s.strip() for s in text.split('.') if s.strip()]
        return sentences
    
    def _detect_repetition(self, sentences: List[str]) -> bool:
        """Detect repetitive structure (chorus/verse pattern)"""
        if len(sentences) < 4:
            return False
        
        # Simple repetition detection
        sentence_hashes = [hash(s.lower()) for s in sentences]
        unique_hashes = set(sentence_hashes)
        
        # If less than 70% are unique, likely has repetition
        return len(unique_hashes) < len(sentences) * 0.7
    
    def _detect_progression(self, text: str) -> str:
        """Detect temporal progression pattern"""
        text_lower = text.lower()
        
        temporal_scores = {}
        for tense, indicators in self.TEMPORAL_INDICATORS.items():
            score = sum(1 for indicator in indicators if indicator in text_lower)
            if score > 0:
                temporal_scores[tense] = score
        
        if not temporal_scores:
            return "abstract"
        
        dominant_tense = max(temporal_scores, key=temporal_scores.get)
        
        tense_mapping = {
            "past": "retrospective",
            "present": "immediate", 
            "future": "aspirational"
        }
        
        return tense_mapping.get(dominant_tense, "abstract")
    
    def _detect_verse_chorus(self, text: str) -> Dict[str, Any]:
        """Detect verse-chorus structure"""
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        if len(lines) < 8:
            return {"detected": False, "structure": "simple"}

        # Look for repeated sections
        line_counts = Counter(lines)
        repeated_lines = [line for line, count in line_counts.items() if count > 1]

        has_chorus = len(repeated_lines) > 0

        return {
            "detected": has_chorus,
            "structure": "verse_chorus" if has_chorus else "narrative",
            "repeated_lines": len(repeated_lines)
        }
    
    def _get_default_structure(self) -> Dict[str, Any]:
        """Default structure for empty/failed analysis"""
        return {
            "sentence_count": 0,
            "has_questions": False,
            "has_exclamations": False,
            "repetitive_structure": False,
            "story_progression": "abstract",
            "verse_chorus_pattern": {"detected": False, "structure": "simple"}
        }

class LyricAnalyzer:
    """Main lyrics analysis coordinator"""
    
    def __init__(self, config):
        self.config = config
        self.emotion_classifier = EmotionClassifier()
        self.theme_extractor = ThemeExtractor()
        self.narrative_analyzer = NarrativeAnalyzer()
        
        # Initialize Whisper model if available
        self.whisper_model = None
        enable_transcription = bool(getattr(config, "enable_transcription", True))
        model_name = str(getattr(config, "whisper_model_size", getattr(config, "model", "base")) or "base")
        if WHISPER_AVAILABLE and enable_transcription:
            try:
                self.whisper_model = whisper.load_model(model_name)
                logger.info(f"Whisper model '{model_name}' loaded")
            except Exception as e:
                logger.error(f"Whisper initialization failed: {e}")
    
    def analyze(self, audio_file: str) -> LyricAnalysis:
        """
        Complete lyric analysis pipeline
        
        Args:
            audio_file: Path to audio file
            
        Returns:
            LyricAnalysis with complete results
        """
        # Transcribe if possible
        transcript = self._transcribe_audio(audio_file)

        if not transcript or len(transcript.strip()) < 10:
            return self._get_default_analysis()

        logger.debug(f"Transcribed {len(transcript)} characters of lyrics")

        # Analyze transcript
        emotions = self.emotion_classifier.classify_emotions(transcript)
        themes = self.theme_extractor.extract_themes(transcript)
        visual_elements = self.theme_extractor.extract_visual_elements(transcript)
        narrative = self.narrative_analyzer.analyze_structure(transcript)
        
        # Calculate emotional intensity
        emotional_intensity = self._calculate_emotional_intensity(emotions)
        
        # Generate Deforum prompts
        deforum_prompts = self._generate_deforum_prompts(emotions, visual_elements)
        
        # Create analysis object
        return LyricAnalysis(
            has_lyrics=True,
            raw_text=transcript,
            emotions=list(emotions.keys()),
            emotion_details=emotions,
            themes=themes,
            visual_elements=visual_elements,
            narrative_structure=narrative,
            emotional_intensity=emotional_intensity,
            deforum_prompts=deforum_prompts,
            word_count=len(transcript.split())
        )
    
    def _transcribe_audio(self, audio_file: str) -> str:
        """Transcribe audio using Whisper"""
        if not self.whisper_model or not WHISPER_AVAILABLE:
            return ""
        
        try:
            transcribe_kwargs = {
                "word_timestamps": False,
                "condition_on_previous_text": False,
                "no_speech_threshold": 0.6,
                "logprob_threshold": -1.0,
            }
            language = getattr(self.config, "language", None)
            if language:
                transcribe_kwargs["language"] = language
            result = self.whisper_model.transcribe(
                audio_file,
                **transcribe_kwargs,
            )
            
            transcript = result.get("text", "").strip()
            
            # Basic quality check
            if len(transcript) < 20:
                return ""
            
            return transcript

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""
    
    def _calculate_emotional_intensity(self, emotions: Dict) -> float:
        """Calculate overall emotional intensity"""
        if not emotions:
            return 0.3
        
        total_confidence = sum(data["confidence"] for data in emotions.values())
        num_emotions = len(emotions)
        
        # Normalize and scale
        avg_confidence = total_confidence / num_emotions if num_emotions > 0 else 0
        
        # Bonus for multiple emotions (complex emotional content)
        complexity_bonus = min(0.2, num_emotions * 0.05)
        
        return min(1.0, avg_confidence + complexity_bonus)
    
    def _generate_deforum_prompts(self, emotions: Dict, visual_elements: List[str]) -> List[str]:
        """Generate Deforum-optimized prompt additions"""
        prompts = []
        
        # Add top emotions
        for emotion, data in sorted(emotions.items(), 
                                  key=lambda x: x[1]["confidence"], 
                                  reverse=True)[:2]:
            prompts.append(data["visual_style"])
        
        # Add visual elements
        if visual_elements:
            element_groups = self._group_visual_elements(visual_elements)
            for group_name, elements in element_groups.items():
                if elements:
                    element_prompt = f"{group_name}: {', '.join(elements[:3])}"
                    prompts.append(element_prompt)
        
        return prompts[:4]  # Limit to prevent prompt overflow
    
    def _group_visual_elements(self, elements: List[str]) -> Dict[str, List[str]]:
        """Group visual elements by category"""
        groups = {category: [] for category in self.theme_extractor.VISUAL_KEYWORDS.keys()}
        
        for element in elements:
            for category, keywords in self.theme_extractor.VISUAL_KEYWORDS.items():
                if element in keywords:
                    groups[category].append(element)
                    break
        
        return {k: v for k, v in groups.items() if v}  # Remove empty groups
    
    def _get_default_analysis(self) -> LyricAnalysis:
        """Default analysis for instrumental or failed transcription"""
        return LyricAnalysis(
            has_lyrics=False,
            raw_text="",
            emotions=[],
            emotion_details={},
            themes=[],
            visual_elements=[],
            narrative_structure=self.narrative_analyzer._get_default_structure(),
            emotional_intensity=0.3,
            deforum_prompts=["abstract visualization", "flowing colors"],
            word_count=0
        )
