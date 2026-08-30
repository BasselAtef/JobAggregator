import numpy as np      
import pandas as pd

df = pd.DataFrame({
    'categories':['Electronics', 'Clothing', 'Groceries', 'Furniture'],
    'purchase_amount': [200, np.nan, 300, 500]})

df['purchase_amount'] = df['purchase_amount'].fillna(
    df.groupby('categories')['purchase_amount'].transform('median')
)


