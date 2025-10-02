import pandas as pd
import csv

replace = pd.read_csv("C:\\Users\\E00279\\OneDrive - neousys-tech\\桌面\\09_LT check\\item name replace.csv")

#"POD"
pod = pd.read_csv("open purchase orders.csv", encoding='utf-8')
pod.drop(columns=['Name', 'Amount', 'Open Balance', "Rcv'd", "Qty", "Memo"], inplace=True)
pod.rename(columns={"Date": "Order Date", "Num": "QB Num", "Source Name": "Name", "Backordered": "Qty(+)"}, inplace=True)
pod.drop(pod.columns[0], axis=1, inplace=True)
pod.dropna(how='all', inplace=True)
pod.dropna(thresh=5, inplace=True) #刪除有效值少於5個的行
pod['Item'] = pod['Item'].str.split(':').str[1]
pod['QB Num'] = pod['QB Num'].str.split('(').str[0]
for col in ['Order Date', 'Deliv Date']:
    pod[col] = pd.to_datetime(pod[col]).dt.strftime('%Y/%m/%d')
pod.to_csv('open purchase2.csv', index=False)


#"NAV"
NAV = pd.read_csv("Sales Date return platform.csv", usecols=['Document No.', "Customer PO No.", "Customer Ordering Model",
                                                             "OP Estimated Shipping Date", "Quantity", "No.",
                                                             "Customer Ordering Desc."], encoding='utf-8')
NAV.rename(columns={"Customer PO No.": "QB Num", "Customer Ordering Model": "Item", 'Document No.': "Remark",
                    "OP Estimated Shipping Date": "Ship Date", "Quantity": "Qty(+)"}, inplace=True)
NAV = NAV[NAV['Item'] != 'Engineer Service- COS']
NAV = NAV[NAV['Item'] != 'CUSTOMER SERVICES']
NAV = NAV[NAV['Item'] != 'FORWARDING CHARGE, EXCLUDING IMPORT DUTY.']
NAV['QB Num'] = NAV['QB Num'].str.split('(').str[0]

NAV.to_csv('NAV1.csv', index=False)

# 讀取 NAV1 並篩選符合條件的數據
s50 = []
with open('NAV1.csv', 'r', encoding='utf-8') as file:
    csv_reader = csv.reader(file)
    data_list = list(csv_reader)

for row in data_list:
    if row[2].startswith("S"):  # 檢查 Item 是否以 "S" 開頭
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
NAV = pd.read_csv("NAV1.csv", usecols=['Remark', 'QB Num', 'Item', 'Qty(+)', 'Ship Date'], encoding='utf-8')
replace_dict = dict(zip(replace['NAV'], replace['QB']))
NAV['Item'] = NAV['Item'].replace(replace_dict)

NAV.to_csv('NAV1.csv', index=False)

# 讀取 open purchase2.csv 並處理數據
a = pd.read_csv('open purchase2.csv', usecols=['QB Num', "Order Date", "Inventory Site", "P. O. #", "Name", "Item"])
a.drop_duplicates(inplace=True)
a['Qty(-)'] = "0"

fil = set(a['Item'])
NAV = NAV[NAV['Item'].isin(fil)]
a = a.drop(columns=["Item"])
a.drop_duplicates(inplace=True)

# 合併 NAV 和 open purchase2.csv
Final = pd.merge(left=NAV, right=a, on=["QB Num"], how="left")
columns = ['Order Date', 'Ship Date', 'QB Num', "P. O. #", "Name", 'Qty(-)', 'Qty(+)', 'Item', 'Inventory Site', 'Remark']
Final.to_csv('Final.csv', index=False, columns=columns)


