import pandas as pd
import numpy as np
from clean_functions.merges_tk import *
from nltk.corpus import stopwords
from google.cloud import storage
import io
import pyarrow.parquet as pq
import pyarrow as pa
import nltk
##
bucket_name = 'datosfuentes'
storage_client = storage.Client(project="proyectrestaurant-447114")
bucket = storage_client.get_bucket(bucket_name)

class DataPipeline:
    def __init__(self, yelp_path, google_path, yelp_reviews_path, google_reviews_path):
        self.df_yelp = pd.read_parquet(yelp_path)
        self.df_google = pd.read_parquet(google_path)
        self.df_rev_yelp = pd.read_parquet(yelp_reviews_path).drop(columns=["useful", "funny", "cool"])
        self.df_rev_goo = pd.read_parquet(google_reviews_path).drop_duplicates(
            subset=['user_id', 'name', 'time', 'rating', 'text', 'pics', 'resp', 'gmap_id']
        )
        self.cleaner = RestaurantDataCleaner()
        self.matcher = FuzzyMatcher()
        self.merger = DataMerger(self.cleaner, self.matcher)
        nltk.download('stopwords')
        self.stop_words = set(stopwords.words('english'))

    def merge_data(self):
        result = self.merger.merge_datasets(self.df_google, self.df_yelp)
        result['id'] = range(1, len(result) + 1)
    
    # Limpieza de la columna hours
        if 'hours' in result.columns:
            result['hours'] = result['hours'].apply(lambda x: x if isinstance(x, list) else [])
        return result


    def process_reviews(self, result):
        grouped_id = result[["id", "id_G", "id_Y"]].drop_duplicates(subset=["id_G", "id_Y"])
        grouped_id.rename(columns={"id": "id_business"}, inplace=True)

        self.df_rev_yelp['origin'] = 'Y'
        self.df_rev_yelp.rename(columns={"stars_y": "stars", "business_id": "id_Y"}, inplace=True)

        self.df_rev_goo['time'] = pd.to_datetime(self.df_rev_goo['time'], unit='ms')
        self.df_rev_goo['origin'] = 'G'
        self.df_rev_goo['review_id'] = range(1, len(self.df_rev_goo) + 1)
        self.df_rev_goo.drop(columns=["name", "pics", "resp"], inplace=True)
        self.df_rev_goo.rename(columns={"rating": "stars", "time": "date", "gmap_id": "id_G"}, inplace=True)

        grouped_id_unique_y = grouped_id[['id_Y', 'id_business']].drop_duplicates()
        grouped_id_unique_g = grouped_id[['id_G', 'id_business']].drop_duplicates()

        df_yelp_merged = pd.merge(self.df_rev_yelp, grouped_id_unique_y, on='id_Y', how='left')
        df_goo_merged = pd.merge(self.df_rev_goo, grouped_id_unique_g, on='id_G', how='left')
        df_rev = pd.concat([df_yelp_merged, df_goo_merged], ignore_index=True).drop(columns=["review_id"])
        df_rev['id'] = range(1, len(df_rev) + 1)
        df_rev['id_business'] = df_rev['id_business'].astype(str)
        df_rev = self.process_business_ids(df_rev)
        buff_rev = io.BytesIO()
        df_rev.to_parquet(buff_rev,index=False,engine='pyarrow')
        buff_rev.seek(0)
        destino="tablas/reviews.parquet"
        blob_rev = bucket.blob(destino)
        blob_rev.upload_from_file(buff_rev)
        return df_rev

    def process_business_ids(self, df):
        df = df.copy()
        df.loc[(df['id_business'].isna()) & (df['id_Y'].notna()), 'id_business'] = df['id_Y']
        df.loc[(df['id_business'].isna()) & (df['id_G'].notna()), 'id_business'] = df['id_G']
        return df.drop(columns=['id_G', 'id_Y'])

    def create_states_and_cities(self, result):
        states = result[["state"]].drop_duplicates().reset_index(drop=True)
        states["id"] = range(1, len(states) + 1)
        states.rename(columns={"state": "state_name"}, inplace=True)
        #carga a gcloud stados
        buff_states = io.BytesIO()
        states.to_parquet(buff_states,index=False,engine='pyarrow')
        buff_states.seek(0)
        destino="tablas/states.parquet"
        blob_states = bucket.blob(destino)
        blob_states.upload_from_file(buff_states)
        #states.to_parquet("ETL/states.parquet", index=False)

        cities = result[["city", "postal_code", "state"]].drop_duplicates().reset_index(drop=True)
        cities = cities.merge(states, left_on="state", right_on="state_name").drop(columns=["state_name", "state"])
        cities.rename(columns={"id": "id_state", "city": "city_name"}, inplace=True)
        cities["id"] = range(1, len(cities) + 1)
        #carga a gcloud
        buff_cities = io.BytesIO()
        cities.to_parquet(buff_cities,index=False,engine='pyarrow')
        buff_cities.seek(0)
        destino="tablas/cities.parquet"
        blob_cities = bucket.blob(destino)
        blob_cities.upload_from_file(buff_cities)
        #cities.to_parquet("ETL/cities.parquet", index=False)

        return states, cities

    def clean_category(self, cat):
        return ' '.join([word for word in cat.split() if word.lower() not in self.stop_words])

    def create_business_tables(self, result, cities_df):
        categories = pd.DataFrame(
            [self.clean_category(cat) for sublist in result["categories"] for cat in sublist],
            columns=["category_name"]
        ).drop_duplicates().reset_index(drop=True)
        categories["id_category"] = range(1, len(categories) + 1)
        #carga a gcloud categories
        buff_categories = io.BytesIO()
        categories.to_parquet(buff_categories,index=False,engine='pyarrow')
        buff_categories.seek(0)
        destino="tablas/categories.parquet"
        blob_categories = bucket.blob(destino)
        blob_categories.upload_from_file(buff_categories)
        #categories.to_parquet("ETL/categories.parquet", index=False)

        business = result[["id", "id_G", "id_Y", "name", "city", "postal_code", "latitude", "longitude"]].copy()
        business = business.merge(
            cities_df[["postal_code", "city_name", "id"]],
            left_on=["postal_code", "city"],
            right_on=["postal_code", "city_name"],
            how="left"
        ).rename(columns={"id_x": "id", "id_y": "id_city"}).drop(columns=["city_name", "city", "postal_code"])
        #carga a gcloud business
        buff_business = io.BytesIO()
        business.to_parquet(buff_business,index=False,engine='pyarrow')
        buff_business.seek(0)
        destino="tablas/business.parquet"
        blob_business = bucket.blob(destino)
        blob_business.upload_from_file(buff_business)
        #business.to_parquet("ETL/business.parquet", index=False)

        business_categories_rows = []
        for idx, row in result.iterrows():
            business_id = row["id"]
            for category in row["categories"]:
                cleaned_category = self.clean_category(category)
                category_id = categories[categories["category_name"] == cleaned_category]["id_category"].iloc[0]
                business_categories_rows.append({
                    "id_business": business_id,
                    "id_category": category_id
                })

        business_categories = pd.DataFrame(business_categories_rows)
        business_categories["id"] = range(1, len(business_categories) + 1)
        #carga a gcloud business_categories
        buff_business_categories = io.BytesIO()
        business_categories.to_parquet(buff_business_categories,index=False,engine='pyarrow')
        buff_business_categories.seek(0)
        destino="tablas/business_categories.parquet"
        blob_business_categories = bucket.blob(destino)
        blob_business_categories.upload_from_file(buff_business_categories)
        #business_categories.to_parquet("ETL/business_categories.parquet", index=False)

        return business, categories, business_categories

    def create_users_table(self, df_rev):
        users = df_rev[["user_id"]].drop_duplicates().reset_index(drop=True).rename(columns={"user_id": "id"})
        #carga a gcloud users
        buff_users = io.BytesIO()
        users.to_parquet(buff_users,index=False,engine='pyarrow')
        buff_users.seek(0)
        destino="tablas/users.parquet"
        blob_users = bucket.blob(destino)
        blob_users.upload_from_file(buff_users)
        #users.to_parquet("ETL/users.parquet", index=False)
        return users
