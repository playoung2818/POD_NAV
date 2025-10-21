import pandas as pd
import csv
from sqlalchemy import create_engine
# Build Supabase engine
DATABASE_DSN = (
    "postgresql://postgres.avcznjglmqhmzqtsrlfg:Czheyuan0227@"
    "aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
)
engine = create_engine(DATABASE_DSN, pool_pre_ping=True)

replace = pd.read_csv("item name replace.csv")

#SO
SO_INV = pd.read_sql_table("wo_structured", con=engine, schema="public")
SO = SO_INV[['Order Date', 'Ship Date', 'QB Num', "P. O. #", "Name",'Qty(+)', 'Qty(-)', 'Item', 'Pre/Bare']]
# SO.to_csv('open sales2.csv',index=False,columns =SO)

#"POD"
pod = pd.read_sql_table("Open_Purchase_Orders", con=engine, schema="public")
# pod.to_csv('open purchase2.csv', index=False)


NAV= pd.read_sql_table("NT Shipping Schedule", con=engine, schema="public")
NAV.to_csv('NAV1.csv', index=False)

# 讀取 NAV1 並篩選符合條件的數據
s50 = []
# with open('NAV1.csv', 'r', encoding='utf-8') as file:
#     csv_reader = csv.reader(file)
#     data_list = list(csv_reader)


for _, row in NAV.iterrows():
    if row['Pre/Bare'] == 'Pre':
        s50.append(row)

result_lists = []
for original_list in s50:
    # 分割字串
    product_str = original_list[-1]
    product_str = product_str.replace('\u00A0', ' ').replace('\u3000', ' ')
    product_info = product_str.split(', including ')
    #product_info = original_list[-1].split(', including ')
    product_info[0] = product_info[0].split(',')[0]  # 產品代碼
    components = product_info[1].split(', ') if len(product_info) > 1 else []

    # 建立各組件的新 list
    for component in components:
        new_list = original_list.copy()
        new_list[-1] = component.strip()
        result_lists.append(new_list)

    # 加入產品代碼
    new_list_with_product_code = original_list.copy()
    new_list_with_product_code[-1] = product_info[0]
    result_lists.append(new_list_with_product_code)

for i in range(0,len(result_lists)):
    result_lists[i][3] = result_lists[i][6]
    
# 調整數據格式
transformed_lists = []
for result_list in result_lists:
    transformed_list = result_list.copy()
    transformed_list[3] = transformed_list[3].replace(" ", "")
    
    if len(transformed_list[3]) > 1 and transformed_list[3][1] == 'x' and transformed_list[3][0].isdigit():
        quantity = int(transformed_list[3].split('x')[0])
        name = transformed_list[3].split('x')[-1]
        transformed_list[3] = name
        transformed_list[4] = str(quantity * float(transformed_list[4]))  # 更新數量

    transformed_lists.append(transformed_list)

# 追加寫入 NAV1
with open('NAV1.csv', 'a+', encoding='utf-8', newline="") as csvfile:
    write = csv.writer(csvfile)
    write.writerows(transformed_lists)

# NAV 加上倉別和日期
# NAV = pd.read_csv("NAV1.csv", usecols=['Remark', 'QB Num', 'Item', 'Qty(+)', 'Ship Date'], encoding='utf-8')
NAV = NAV[['QB Num', 'Item', 'Qty(+)', 'Ship Date']]
replace_dict = dict(zip(replace['NAV'], replace['QB']))
NAV['Item'] = NAV['Item'].replace(replace_dict)
NAV.to_csv('NAV1.csv', index=False)

# # 讀取 open purchase2.csv 並處理數據
# a = pd.read_csv('open purchase2.csv', usecols=['QB Num', "Order Date", "Inventory Site", "P. O. #", "Name", "Item"])
# a.drop_duplicates(inplace=True)
# a['Qty(-)'] = "0"
# fil = set(a['Item'])
# NAV = NAV[NAV['Item'].isin(fil)]
# a = a.drop(columns=["Item"])
# a.drop_duplicates(inplace=True)

# # 合併 NAV 和 open purchase2.csv
# Final = pd.merge(left=NAV, right=a, on=["QB Num"], how="left")
# missing_qb_num = set(a["QB Num"]) - set(Final["QB Num"])
# #print("消失的 QB Num:", missing_qb_num)
# missing_rows = a[a["QB Num"].isin(missing_qb_num)]
# missing_rows = pd.merge(left=pod, right=missing_rows, on=["QB Num","Order Date","P. O. #","Inventory Site","Name"], how="inner")
# missing_rows.to_csv('missing_rows.csv', index=False)
# columns = ['Order Date', 'Ship Date', 'QB Num', "P. O. #", "Name", 'Qty(-)', 'Qty(+)', 'Item', 'Inventory Site', 'Remark']
# Final.to_csv('Final.csv', index=False, columns=columns)

# #手動補回 Final
# Final = pd.concat([Final, missing_rows], ignore_index=True)


# # 定義 CSV 欄位順序
# columns = ['Order Date', 'Ship Date', 'QB Num', "P. O. #", "Name", 'Qty(-)', 'Qty(+)', 'Item', 'Inventory Site', 'Remark']


# # 合併 SO 和 Final
# SONAV = pd.concat([SO, Final])
# SONAV = SONAV.sort_values(by=["Inventory Site", "Item", "Ship Date"], ascending=False)
# SONAV = SONAV.dropna(subset=['Item'])

# # 儲存 SONAV 結果
# SONAV.to_csv("SONAV.csv", index=False, columns=columns)

