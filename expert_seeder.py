
import logging
from sqlalchemy.orm import Session
from db import SessionLocal
from models_db import Disease

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expert_seeder")

EXPERT_DATA = [
    # BANANA
    {
        "plant": "Banana",
        "disease": "Sigatoka",
        "desc": "Sigatoka is a fungal disease that affects banana leaves, causing significant yield loss.",
        "prev": "Ensure proper drainage, avoid high density planting, and remove infected leaves regularly.",
        "symp": "Yellow spots on leaves that turn into long, dark brown streaks with gray centers.",
        "chem": "Apply Propiconazole or Mancozeb as per recommended dosage during rainy seasons.",
        "org": "Apply Neem oil sprays (3%) or Pseudomonas fluorescens based bio-fungicides."
    },
    {
        "plant": "Banana",
        "disease": "Panama Wilt",
        "desc": "A lethal vascular wilt disease caused by the soil-borne fungus Fusarium oxysporum.",
        "prev": "Use resistant varieties, maintain soil pH around 6.5, and avoid movement of infected soil.",
        "symp": "Progressive yellowing of older leaves, followed by collapse at the petiole base.",
        "chem": "No direct chemical cure; soil drenching with Carbendazim (0.2%) can limit spread.",
        "org": "Apply Bio-agents like Trichoderma viride or Pseudomonas fluorescens to the soil."
    },
    # RICE
    {
        "plant": "Rice",
        "disease": "Blast",
        "desc": "One of the most destructive diseases of rice caused by the fungus Magnaporthe oryzae.",
        "prev": "Avoid excessive nitrogen fertilizers, maintain proper water levels, and use resistant seeds.",
        "symp": "Spindle-shaped spots on leaves with gray centers and brown borders. Rotten necks on panicles.",
        "chem": "Spray Tricyclazole (0.6 g/L) or Carbendazim (1.0 g/L) at first appearance.",
        "org": "Spray with cow urine and vitex leaf extract or Pseudomonas powder."
    },
    {
        "plant": "Rice",
        "disease": "Bacterial Blight",
        "desc": "A serious bacterial disease that reduces grain yield and quality significantly.",
        "prev": "Avoid field flooding from infected areas, keep fields clean, and use balanced fertilization.",
        "symp": "Water-soaked streaks that turn yellow and then white-gray, starting from leaf tips.",
        "chem": "Spray Streptocycline (0.1 g/L) mixed with Copper Oxychloride (2 g/L).",
        "org": "Use balanced potash fertilization and spray with fresh cow dung extract (5%)."
    },
    # COCONUT
    {
        "plant": "Coconut",
        "disease": "Bud Rot",
        "desc": "A fatal fungal disease (Phytophthora palmivora) common in high rainfall areas.",
        "prev": "Improve drainage, remove dead palms, and apply Bordeaux paste to the crown base.",
        "symp": "Yellowing of the youngest leaf (spindle leaf), followed by drooping and rotting at the base.",
        "chem": "Apply Bordeaux paste to the bud region or drench with Mancozeb (3g/L).",
        "org": "Apply 1% Bordeaux mixture spray before and after monsoon rains."
    },
    {
        "plant": "Coconut",
        "disease": "Stem Bleeding",
        "desc": "A fungal disease causing reddish-brown liquid to ooze from the stem cracks.",
        "prev": "Avoid trunk injuries, maintain soil moisture, and apply balanced organic manure.",
        "symp": "Reddish-brown liquid oozes from the trunk; tissues inside the trunk turn dark and rot.",
        "chem": "Chisel out infected tissue and apply coal tar or 10% Bordeaux paste to the wound.",
        "org": "Apply Trichoderma enriched neem cake to the basin and ensure proper irrigation."
    },
    # CHILLI
    {
        "plant": "Chilli",
        "disease": "Leaf Curl",
        "desc": "Caused by viruses usually transmitted by whiteflies or thrips.",
        "prev": "Control whiteflies, use yellow sticky traps, and remove infected plants immediately.",
        "symp": "Leaves curl upwards or downwards, become small, thickened, and brittle.",
        "chem": "Spray Imidacloprid (0.3ml/L) or Acetamiprid (0.2g/L) to control vectors.",
        "org": "Spray Neem oil (5ml/L) or Garlic-Chilli extract regularly."
    },
    # TOMATO
    {
        "plant": "Tomato",
        "disease": "Early Blight",
        "desc": "A common fungal disease (Alternaria solani) affecting foliage, stems, and fruits.",
        "prev": "Practice crop rotation, provide adequate spacing, and avoid overhead irrigation.",
        "symp": "Small brown spots on older leaves that expand into 'target-like' concentric rings.",
        "chem": "Spray Chlorothalonil or Mancozeb every 7-10 days upon detection.",
        "org": "Apply mulch to prevent soil splash and use compost tea or bio-fungicides."
    },
    {
        "plant": "Tomato",
        "disease": "Late Blight",
        "desc": "A rapid and devastating disease caused by Phytophthora infestans.",
        "prev": "Use resistant varieties, ensure dry foliage, and destroy all potato/tomato debris.",
        "symp": "Dark, water-soaked patches on leaves that turn brown/black; white mold may appear below.",
        "chem": "Standard application of Copper-based fungicides or Metalaxyl-M.",
        "org": "Use 1% Bordeaux mixture or Copper Hydroxide for protective coverage."
    },
    # MANGO
    {
        "plant": "Mango",
        "disease": "Anthracnose",
        "desc": "A common fungal disease (Colletotrichum gloeosporioides) affecting leaves, twigs, and fruits.",
        "prev": "Prune infected twigs, ensure good canopy aeration, and destroy fallen leaves.",
        "symp": "Small, dark-colored spots on leaves and flowers; large sunken black spots on fruits.",
        "chem": "Spray Carbendazim (1g/L) or Copper Oxychloride (3g/L) during flowering.",
        "org": "Apply hot water treatment (52°C for 5-10 mins) for harvested fruits."
    },
    {
        "plant": "Mango",
        "disease": "Powdery Mildew",
        "desc": "A serious disease that affects the inflorescence and leads to significant fruit drop.",
        "prev": "Spray wettable sulphur periodically during the pre-monsoon season.",
        "symp": "Whitish powdery growth on flowers, leaves, and young fruits; flowers turn brown and dry.",
        "chem": "Apply Wettable Sulphur (2g/L) or Dinocap (1ml/L) as soon as symptoms appear.",
        "org": "Dust with fine sulphur powder early in the morning or spray with milk-water mixture."
    },
    # CORN
    {
        "plant": "Corn",
        "disease": "Northern Leaf Blight",
        "desc": "A fungal disease that can cause significant yield loss in moist, temperate conditions.",
        "prev": "Rotate crops with non-grass species and use resistant hybrids.",
        "symp": "Long, cigar-shaped, gray-green to tan lesions on the lower leaves first.",
        "chem": "Apply Azoxystrobin or Pyraclostrobin based fungicides if infection is severe.",
        "org": "Practice deep tillage to bury crop debris and ensure proper field drainage."
    },
    # WHEAT
    {
        "plant": "Wheat",
        "disease": "Stripe Rust",
        "desc": "Also known as yellow rust, it can spread rapidly across large areas.",
        "prev": "Use resistant cultivars and avoid early sowing in high-risk zones.",
        "symp": "Small, yellow-orange pustules arranged in long stripes along the leaf veins.",
        "chem": "Apply Propiconazole (1ml/L) or Tebuconazole (1.5ml/L) at the first sign of rust.",
        "org": "Early detection and destruction of volunteer wheat plants to break the cycle."
    },
    # POTATO
    {
        "plant": "Potato",
        "disease": "Late Blight",
        "desc": "The most destructive potato disease globally, caused by Phytophthora infestans.",
        "prev": "Use certified disease-free tubers and ensure hilling to protect tubers from soil spores.",
        "symp": "Water-soaked lesions on leaf tips turning dark brown; white fuzzy growth on undersides in humid conditions.",
        "chem": "Preventive spray of Mancozeb or systemic spray of Cymoxanil + Mancozeb.",
        "org": "Use resistant varieties and apply Copper-based sprays before rains."
    }
]

def seed_expert_data():
    db = SessionLocal()
    try:
        for entry in EXPERT_DATA:
            # Check if this plant-disease already exists (substring matching for safety)
            existing = db.query(Disease).filter(
                Disease.plant_name.ilike(f"%{entry['plant']}%"),
                Disease.disease_name.ilike(f"%{entry['disease']}%")
            ).first()
            
            if existing:
                logger.info(f"Updating existing: {entry['plant']} - {entry['disease']}")
                existing.description = entry['desc']
                existing.prevention = entry['prev']
                existing.symptoms = entry['symp']
                existing.chemical_remedy = entry['chem']
                existing.organic_remedy = entry['org']
                existing.remedy = f"Chemical: {entry['chem']}\\nOrganic: {entry['org']}"
            else:
                logger.info(f"Creating new expert entry: {entry['plant']} - {entry['disease']}")
                new_d = Disease(
                    plant_name=entry['plant'],
                    disease_name=entry['disease'],
                    description=entry['desc'],
                    prevention=entry['prev'],
                    symptoms=entry['symp'],
                    chemical_remedy=entry['chem'],
                    organic_remedy=entry['org'],
                    remedy=f"Chemical: {entry['chem']}\\nOrganic: {entry['org']}"
                )
                db.add(new_d)
        
        db.commit()
        logger.info("✅ Expert Knowledge Seeding Complete!")
    except Exception as e:
        db.rollback()
        logger.error(f"Seeding failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_expert_data()
