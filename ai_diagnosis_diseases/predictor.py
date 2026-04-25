import os
import numpy as np
import tensorflow as tf
from PIL import Image
import logging

logger = logging.getLogger("ai-diagnosis")

class DiseasePredictor:
    def __init__(self, model_path: str = None):
        """
        Initializes the DiseasePredictor by manually building the architecture
        and loading weights. This avoids Keras vision mismatch errors.
        """
        self.model_path = model_path or "ai_diagnosis_diseases/models/efficient_expert.h5"
        self.labels_path = "ai_diagnosis_diseases/models/labels.txt"
        self.input_shape = (224, 224)
        self.class_names = self._load_labels()
        
        num_classes = len(self.class_names) if self.class_names else 115
        
        try:
            if os.path.exists(self.model_path):
                logger.info(f"Loading model from {self.model_path}")
                # Use standard load_model - most reliable if architecture is saved in .h5
                self.model = tf.keras.models.load_model(self.model_path, compile=False)
                logger.info("✅ AI Model initialized successfully.")
            else:
                logger.error(f"Model file NOT FOUND at {self.model_path}")
                self.model = None
        except Exception as e:
            logger.error(f"Critical failure loading AI Model: {e}")
            self.model = None

    def _load_labels(self):
        """Loads class names from labels.txt"""
        if os.path.exists(self.labels_path):
            with open(self.labels_path, 'r') as f:
                return [line.strip() for line in f.readlines()]
        logger.warning("labels.txt not found.")
        return None

    def preprocess_image(self, image_bytes: bytes):
        """
        Converts raw bytes to a 224x224 tensor.
        """
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img = img.resize(self.input_shape)
        img_array = np.array(img).astype(np.float32)
        img_array = np.expand_dims(img_array, axis=0)
        return img_array

    def predict(self, image_bytes: bytes, crop_hint: str = None):
        """
        Predicts the disease from the image with Total 'Context Isolation'.
        Implements 'Confusion Detection' and 'Strict Lock-In': If a hint is provided,
        we ONLY allow results from that specific crop, suppressing everything else.
        """
        if self.model is None:
            return "Error: Model Not Loaded", 0.0, False
            
        processed_img = self.preprocess_image(image_bytes)
        raw_predictions = self.model.predict(processed_img, verbose=0)[0]
        
        # 1. Capture Raw Instinct (Before any biasing)
        raw_idx = np.argmax(raw_predictions)
        raw_label = self.class_names[raw_idx].lower()
        
        predictions = raw_predictions.copy()
        is_confused = False # Tracks if the AI is fundamentally confused about the plant type
        
        # --- TOTAL CONTEXT ISOLATION (STRICT LOCK-IN) ---
        if crop_hint and crop_hint.lower() not in ["custom", "other"]:
            hint = crop_hint.lower().strip()
            
            # --- SYNONYM MAPPING ---
            target_keywords = [hint]
            if "paddy" in hint: target_keywords.append("rice")
            if "pepper" in hint or "chilli" in hint: 
                target_keywords.extend(["chilli", "pepper"])
            if "maize" in hint or "corn" in hint:
                target_keywords.extend(["corn", "maize"])

            logger.info(f"Applying TOTAL Context Isolation for: {hint} (Targets: {target_keywords})")
            
            # Confusion Check: Did the AI's first guess match ANY of the target keywords?
            matched_hint = any(tk in raw_label for tk in target_keywords)
            
            if not matched_hint:
                # Specific dataset exceptions for confusion check
                exception = False
                if "mango" in target_keywords and ("mango" in raw_label): exception = True
                if "banana" in target_keywords and ("banana" in raw_label or "sigatoka" in raw_label): exception = True
                
                if not exception:
                    is_confused = True
                    logger.warning(f"AI Confusion Detected! Instinct: {raw_label} vs Hint Keywords: {target_keywords}")

            # --- THE STRICT LOCK-IN ---
            # Every single label is either TARGET or SUPPRESSED.
            for i, name in enumerate(self.class_names):
                name_low = name.lower()
                
                # Identify if this label is our target crop
                is_target = any(tk in name_low for tk in target_keywords)
                
                if is_target:
                    # DISEASE-FIRST BIASING
                    if 'healthy' in name_low:
                        # Conservative boost for "Healthy"
                        predictions[i] *= 2.0 
                    else:
                        # Aggressive 35x boost for specific Diseases
                        predictions[i] *= 35.0
                else:
                    # TOTAL SUPPRESSION: Any label that isn't our target is deleted
                    predictions[i] *= 0.0001

        # Normalize predictions after steering (Simple Divide-by-Sum)
        sum_preds = np.sum(predictions)
        if sum_preds > 0:
            norm_predictions = predictions / sum_preds
        else:
            norm_predictions = predictions # Fallback
            
        top_idx = np.argmax(norm_predictions)
        label = self.class_names[top_idx]
        confidence = norm_predictions[top_idx]
        
        # If AI was fundamentally confused about the plant type, cap the confidence to 55%
        if is_confused and confidence > 0.55:
            confidence = 0.55
            
        return label, float(confidence), is_confused

    def softmax(self, x):
        """Compute softmax values for each sets of scores in x."""
        e_x = np.exp(x - np.max(x))
        return e_x / e_x.sum(axis=0)
            
    def predict_consensus(self, image_contents: list[bytes], crop_hint: str = None):
        """
        Takes multiple images (e.g. Full Plant, Leaf, Stem, Root) and 
        performs a high-confidence consensus analysis.
        Returns: (master_label, master_conf, individual_results)
        """
        if not image_contents:
            return "No images provided", 0.0, []
            
        individual_results = []
        for img in image_contents:
            try:
                label, conf, confused = self.predict(img, crop_hint=crop_hint)
                individual_results.append((label, conf, confused))
            except Exception as e:
                logger.error(f"Consensus error on sub-image: {e}")
                individual_results.append(("Error", 0.0, False))
                
        if not individual_results:
            return "Analysis Failed", 0.0, []
            
        # 1. Sort by confidence descending to find the strongest signal
        sorted_results = sorted(individual_results, key=lambda x: x[1], reverse=True)
        best_label, best_conf, master_confused = sorted_results[0]
        
        # 2. Consensus Boost: If other images also found the same EXACT disease, 
        # it's a very strong signal.
        matches = [r for r in individual_results if r[0] == best_label]
        # Skip the best_conf itself for bonus calculation
        bonus = (len(matches) - 1) * 0.10 
        
        final_conf = min(0.99, best_conf + bonus)
        
        return best_label, final_conf, individual_results, master_confused

# Global instance
predictor_instance = DiseasePredictor()
