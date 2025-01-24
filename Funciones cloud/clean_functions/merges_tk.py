from fuzzywuzzy import fuzz
from scipy.spatial import cKDTree
from typing import List, Optional, Tuple
import logging
from haversine import haversine
import pandas as pd
import numpy as np
##
def fuzzy_merge(
    df_google: pd.DataFrame,
    df_yelp: pd.DataFrame,
    name_threshold: int = 70,
    distance_threshold: float = 0.5,
    address_threshold: int = 60,
    name_col: str = 'name',
    address_col: str = 'address',
    lat_col: str = 'latitude',
    lon_col: str = 'longitude',
    comparison_method: str = 'ratio',
    log_level: str = 'INFO'
) -> pd.DataFrame:
    """
    Realiza un merge difuso eficiente entre dos DataFrames usando coincidencias aproximadas
    de nombres, direcciones y distancia geográfica.
    
    Args:
        df_google: Primer DataFrame (Google)
        df_yelp: Segundo DataFrame (Yelp)
        name_threshold: Umbral de similitud para nombres (0-100)
        distance_threshold: Distancia máxima en kilómetros para considerar coincidencia
        address_threshold: Umbral de similitud para direcciones (0-100)
        name_col: Nombre de la columna que contiene los nombres de los lugares
        address_col: Nombre de la columna que contiene las direcciones
        lat_col: Nombre de la columna de latitud
        lon_col: Nombre de la columna de longitud
        comparison_method: Método de comparación de texto ('ratio', 'partial_ratio', 'token_sort_ratio')
        log_level: Nivel de logging ('INFO', 'DEBUG', 'WARNING', etc.)
    
    Returns:
        DataFrame con los registros coincidentes y métricas de similitud
    """
    # Configurar logging
    logging.basicConfig(level=log_level)
    logger = logging.getLogger(__name__)

    # Reset índices y crear mapeo de índices originales
    df_google = df_google.reset_index(drop=True)
    df_yelp = df_yelp.reset_index(drop=True)

    # Validar columnas requeridas
    required_cols = [name_col, address_col, lat_col, lon_col]
    for df, name in [(df_google, 'Google'), (df_yelp, 'Yelp')]:
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Columnas faltantes en DataFrame {name}: {missing}")

    # Validar coordenadas
    for df, name in [(df_google, 'Google'), (df_yelp, 'Yelp')]:
        invalid_lat = ~df[lat_col].between(-90, 90)
        invalid_lon = ~df[lon_col].between(-180, 180)
        if invalid_lat.any() or invalid_lon.any():
            raise ValueError(f"Coordenadas inválidas en DataFrame {name}")

    # Función de limpieza de texto mejorada
    def clean_text(text: str) -> str:
        if not isinstance(text, str):
            return ""
        return ' '.join(text.lower().split())

    # Preparar DataFrames
    df_google['clean_name'] = df_google[name_col].apply(clean_text)
    df_yelp['clean_name'] = df_yelp[name_col].apply(clean_text)
    df_google['clean_address'] = df_google[address_col].apply(clean_text)
    df_yelp['clean_address'] = df_yelp[address_col].apply(clean_text)

    # Crear árboles KD para búsqueda espacial eficiente
    coords_google = np.deg2rad(df_google[[lat_col, lon_col]].values)
    coords_yelp = np.deg2rad(df_yelp[[lat_col, lon_col]].values)

    tree = cKDTree(coords_yelp)

    # Convertir umbral de distancia de km a radianes
    R = 6371  # Radio de la Tierra en km
    distance_rad = distance_threshold / R

    # Encontrar pares de puntos cercanos
    pairs = tree.query_ball_point(coords_google, distance_rad)

    logger.info(f"Encontrados {sum(len(p) for p in pairs)} pares dentro del umbral de distancia")

    # Función para obtener el método de comparación
    comparison_funcs = {
        'ratio': fuzz.ratio,
        'partial_ratio': fuzz.partial_ratio,
        'token_sort_ratio': fuzz.token_sort_ratio
    }
    compare_func = comparison_funcs.get(comparison_method, fuzz.ratio)

    matches = []
    used_yelp_indices = set()

    for idx1, nearby_indices in enumerate(pairs):
        if not nearby_indices:
            continue

        name1 = df_google['clean_name'].iloc[idx1]
        address1 = df_google['clean_address'].iloc[idx1]

        # Calcular similitudes de nombre y dirección para lugares cercanos
        similarities = []
        for idx2 in nearby_indices:
            if idx2 in used_yelp_indices:  # Evitar duplicados
                continue

            name_sim = compare_func(name1, df_yelp['clean_name'].iloc[idx2])
            if name_sim >= name_threshold:
                address_sim = compare_func(address1, df_yelp['clean_address'].iloc[idx2])
                if address_sim >= address_threshold:
                    similarities.append((idx2, name_sim, address_sim))

        if not similarities:
            continue

        # Encontrar la mejor coincidencia basada en el promedio de similitudes
        best_match = max(similarities, key=lambda x: (x[1]*0.7 + x[2]*0.3))
        idx2, name_sim, address_sim = best_match

        coords1 = (df_google[lat_col].iloc[idx1], df_google[lon_col].iloc[idx1])
        coords2 = (df_yelp[lat_col].iloc[idx2], df_yelp[lon_col].iloc[idx2])

        matches.append({
            'google_idx': idx1,
            'yelp_idx': idx2,
            'name_similarity': name_sim,
            'address_similarity': address_sim,
            'distance_km': haversine(coords1, coords2)
        })

        # Marcar índice de Yelp como usado
        used_yelp_indices.add(idx2)

    logger.info(f"Encontradas {len(matches)} coincidencias finales")

    # Crear DataFrame resultante
    if matches:
        matches_df = pd.DataFrame(matches)

        # Usar iloc para acceder a los índices de manera segura
        df_google_matched = df_google.iloc[matches_df['google_idx']]
        df_yelp_matched = df_yelp.iloc[matches_df['yelp_idx']]

        df_merged = pd.concat([
            df_google_matched.reset_index(drop=True),
            df_yelp_matched.add_suffix('_yelp').reset_index(drop=True),
            matches_df[['name_similarity', 'address_similarity', 'distance_km']].reset_index(drop=True)
        ], axis=1)

        return df_merged.drop(columns=['clean_name', 'clean_name_yelp', 'clean_address', 'clean_address_yelp'])

    return pd.DataFrame().drop_duplicates(subset="id_G", inplace=True)


class RestaurantDataCleaner:
    def __init__(self, usa_lat_bounds=(24.396308, 49.384358), 
                 usa_lon_bounds=(-125.0, -66.93457)):
        self.valid_states = ['PA', 'FL', 'NC', 'NY', 'TN', 'GA', 'MA', 'VA', 'NJ', 'MD']
        self.usa_bounds = {'lat': usa_lat_bounds, 'lon': usa_lon_bounds}
        
    def clean_yelp_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[
            (df["state"].isin(self.valid_states)) &
            (df["latitude"].between(*self.usa_bounds['lat'])) &
            (df["longitude"].between(*self.usa_bounds['lon']))
        ]
        df = df.rename(columns={"business_id": "id_Y"})
        df["id_G"] = np.nan
        df = df.drop(columns=["attributes", "stars", "review_count", "is_open"])
        df["categories"] = df["categories"].apply(lambda x: [item.strip() for item in x.split(",")])
        return df
    
    def clean_google_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.rename(columns={"gmap_id": "id_G"})
        df = df.drop(columns=["stars", "review_count"])
        df["id_Y"] = np.nan
        return df

class FuzzyMatcher:
    def __init__(self, name_threshold=75, address_threshold=80, 
                 distance_threshold=0.05, comparison_method='token_sort_ratio'):
        self.thresholds = {
            'name': name_threshold,
            'address': address_threshold,
            'distance': distance_threshold
        }
        self.comparison_method = comparison_method
        
    def _clean_text(self, text: str) -> str:
        return ' '.join(str(text).lower().split()) if isinstance(text, str) else ""
        
    def merge(self, df_google: pd.DataFrame, df_yelp: pd.DataFrame) -> pd.DataFrame:
        df_merged = fuzzy_merge(
            df_google, df_yelp,
            name_threshold=self.thresholds['name'],
            address_threshold=self.thresholds['address'],
            distance_threshold=self.thresholds['distance'],
            comparison_method=self.comparison_method
        )
        return self._process_merged_data(df_merged)
    
    def _process_merged_data(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
            
        columns_to_keep = [
            "id_G", "id_Y_yelp", "name", "address", "city", "state",
            "postal_code", "latitude", "longitude", "categories",
            "categories_yelp", "hours_yelp"
        ]
        df = df[columns_to_keep].rename(columns={"id_Y_yelp": "id_Y"})
        df['business_category'] = df.apply(self._merge_categories, axis=1)
        df = df.drop(columns=['categories', 'categories_yelp'])
        return df.rename(columns={'business_category': 'categories', 'hours_yelp': 'hours'})
    
    def _merge_categories(self, row: pd.Series) -> List[str]:
        google_cats = row['categories'] if isinstance(row['categories'], list) else []
        yelp_cats = row['categories_yelp'] if isinstance(row['categories_yelp'], list) else []
        merged = google_cats + yelp_cats
        return list(dict.fromkeys(merged))

class DataMerger:
    def __init__(self, cleaner: RestaurantDataCleaner, matcher: FuzzyMatcher):
        self.cleaner = cleaner
        self.matcher = matcher
        
    def merge_datasets(self, df_google: pd.DataFrame, df_yelp: pd.DataFrame) -> pd.DataFrame:
        df_yelp_clean = self.cleaner.clean_yelp_data(df_yelp)
        df_google_clean = self.cleaner.clean_google_data(df_google)
        df_merged = self.matcher.merge(df_google_clean, df_yelp_clean)
        result = pd.concat([df_merged, df_google_clean, df_yelp_clean], ignore_index=True)
        return result.drop_duplicates(subset=['id_G'], keep='first', ignore_index= True)