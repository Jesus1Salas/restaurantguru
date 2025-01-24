import pandas as pd
import numpy as np
from google.cloud import storage
from clean_functions.etl_functs import DataPipeline
import base64
import gc
import warnings

def load_parquet_from_gcs(bucket_name, file_name):
    #carga un archivo parquet desde un bucket de google cloud storage
    storage_client = storage.Client(project="proyectrestaurant-447114")
    bucket = storage_client.bucket(bucket_name)    
    blob = bucket.blob(file_name)
    with blob.open("rb") as f:
        return  pd.read_parquet(f)



def main(data, context):

    warnings.filterwarnings("ignore")
    # Definir el nombre del bucket
    bucket_name = 'datosfuentes'
    # Definir los paths de los archivos en el bucket
    y_path = "yelp/filtered_restaurants.parquet"
    g_path = "google/filtered_top_10_states.parquet"
    y_r_path = "yelp/review_filtered.parquet"
    g_r_path = "google/merged_reviews_clean.parquet"

    try:
        if 'data' in data:
            pubsub_message = base64.b64decode(data['data']).decode('utf-8')
            print(f"Mensaje recibido de Pub/Sub: {pubsub_message}")
        else:
            print("No se recibió ningún mensaje en el evento de Pub/Sub.")

        yelp_path = load_parquet_from_gcs(bucket_name, y_path)
        google_path = load_parquet_from_gcs(bucket_name, g_path)
        yelp_reviews_path = load_parquet_from_gcs(bucket_name, y_r_path)
        google_reviews_path = load_parquet_from_gcs(bucket_name, g_r_path)

        '''
        # Descargar archivos del bucket
        blob = bucket.blob(y_path)
        yelp_path = io.BytesIO()
        blob.download_to_file(yelp_path)
        yelp_path.seek(0)

        blob1 = bucket.blob(g_path)
        google_path = io.BytesIO()
        blob1.download_to_file(google_path)

        blob2 = bucket.blob(y_r_path)
        yelp_reviews_path = io.BytesIO()
        blob2.download_to_file(yelp_reviews_path)
        yelp_reviews_path.seek(0)

        blob3 = bucket.blob(g_r_path)
        google_reviews_path = io.BytesIO()
        blob3.download_to_file(google_reviews_path)
        google_reviews_path.seek(0)
        '''
        # Inicializar y ejecutar el pipeline
        pipeline = DataPipeline(yelp_path, google_path, yelp_reviews_path, google_reviews_path)

        # Ejecutar las operaciones del pipeline
        print("Realizando merge de datos...")
        result = pipeline.merge_data()

        print("Procesando reviews...")
        df_reviews = pipeline.process_reviews(result)

        print("Creando tablas de estados y ciudades...")
        states, cities = pipeline.create_states_and_cities(result)

        print("Generando tablas de negocio y categorías...")
        business, categories, business_categories = pipeline.create_business_tables(result, cities)

        print("Creando tabla de usuarios...")
        users = pipeline.create_users_table(df_reviews)

        print("Proceso completado con éxito.")

        del yelp_path, google_path, yelp_reviews_path, google_reviews_path
        del result,df_reviews,states,cities,business,categories,business_categories,users
        gc.collect()

    except Exception as e:
        print(f"Error: {e}")
        raise
    
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))  # Usa el puerto definido por Cloud Run
