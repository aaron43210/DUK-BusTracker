# ml-training-job/train_job.py
import os
import joblib
import pandas as pd
import logging
from sqlalchemy import create_engine
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
MODEL_DIR = os.environ.get("MODEL_DIR", "/app/models")

def train():
    logger.info("Starting ML training job...")
    engine = create_engine(DATABASE_URL)
    
    # In reality, you'd pull historical trip segments. Creating dummy data for bootstrap.
    query = "SELECT * FROM gps_realtime LIMIT 10" 
    
    try:
        # Dummy dataset since we might not have historical data yet
        import numpy as np
        N = 1000
        df = pd.DataFrame({
            "distance_km": np.random.uniform(0.1, 10.0, N),
            "time_of_day_min": np.random.randint(400, 1200, N),
            "day_of_week": np.random.randint(0, 7, N),
            "is_raining": np.random.randint(0, 2, N),
            "is_traffic_heavy": np.random.randint(0, 2, N),
        })
        # Base time: 2 min per km + traffic + rain + noise
        df["duration_min"] = (
            df["distance_km"] * 2.5 
            + df["is_traffic_heavy"] * 5.0 
            + df["is_raining"] * 3.0 
            + np.random.normal(0, 1, N)
        )
        
        X = df.drop("duration_min", axis=1)
        y = df["duration_min"]
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        model = GradientBoostingRegressor(n_estimators=100, learning_rate=0.1)
        model.fit(X_scaled, y)
        
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(model, os.path.join(MODEL_DIR, "eta_model.pkl"))
        joblib.dump(scaler, os.path.join(MODEL_DIR, "eta_scaler.pkl"))
        
        logger.info("Successfully trained and saved model")
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
        
if __name__ == "__main__":
    train()
