import streamlit as st
import pandas as pd
import json
from datetime import datetime
from shapely.wkt import loads
from shapely.geometry.polygon import orient

# Funktion för att formatera datum om det finns ett värde
def format_date(date_str, default_time="00:00:00"):
    if date_str:  # Om parametern inte är tom
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # Hantera standardtider för olika fält
        if default_time == "23:59:59":
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""  # Returnera tom sträng om datum är tomt

# Funktion för att skapa språklista
def create_language_list(row, en_col, se_col, name_attr="text"):
    return [
        {name_attr: row[en_col], "lang": "en-GB"},
        {name_attr: row[se_col], "lang": "se-SE"}
    ] if pd.notna(row[en_col]) and pd.notna(row[se_col]) else (
        [{name_attr: row[en_col], "lang": "en-GB"}] if pd.notna(row[en_col]) else
        [{name_attr: row[se_col], "lang": "se-SE"}] if pd.notna(row[se_col]) else None
    )

# Funktion för att skapa geojson-funktioner
def create_geojson_feature(row):
    geometry = row.get('geometry')
    if isinstance(geometry, str):  # Om geometrin är i WKT-format
        shapely_obj = loads(geometry)
        geometry_type = shapely_obj.geom_type
        geom = {
            "type": geometry_type,
            "coordinates": [],
            "layer": {}
        }

        if geometry_type == 'Point':
            geom["coordinates"] = list(shapely_obj.coords)[0]
            geom["extent"] = {"subType": "Circle", "radius": row['radius']}
        elif geometry_type == 'Polygon':
            oriented_polygon = orient(shapely_obj)
            geom["coordinates"] = [list(oriented_polygon.exterior.coords)]
        else:
            raise ValueError(f"Okänd geometrityp: {geometry_type}")
    else:  # Om geometrin redan är i JSON-format
        geom = geometry

    geom["layer"] = {
        "upper": row.upper,
        "upperReference": row.upperRef,
        "lower": row.lower,
        "lowerReference": row.lowerRef,
        "uom": row.uom,
    }

    auth = {'name': row['authority_name']}
    auth_cols = ['purpose', 'email', 'siteURL', 'phone', 'intervalBefore']

    for col in auth_cols:
        val = row[f'authority_{col}']
        if not isinstance(val, list) and pd.notna(val):
            auth[col] = val

    contact_name = row.get('authority_contactName')
    if pd.notna(contact_name):
        auth['contactName'] = [{'text': contact_name, 'lang': 'se-SE'}]

    service = row.get('service')
    if pd.notna(service):
        auth['service'] = [{'text': service, 'lang': 'se-SE'}]

    times = None

    if pd.notna(row['startDateTime']) or pd.notna(row['endDateTime']) or pd.notna(row['schedule']):
        times = {}
        if pd.notna(row['startDateTime']):
            times['startDateTime'] = row['startDateTime']
        if pd.notna(row['endDateTime']):
            times['endDateTime'] = row['endDateTime']
        if pd.notna(row['schedule']):
            times['schedule'] = json.loads(row['schedule'])

    dataSource = {'creationDateTime': row['creationDateTime']}
    if pd.notna(row['updateDateTime']):
        dataSource['updateDateTime'] = row['updateDateTime']
    if pd.notna(row['originator']):
        dataSource['originator'] = row['originator']

    feature = {
        'type': 'Feature',
        'geometry': geom,
        'properties': {
            'identifier': row['identifier'],
            'country': row['country'],
            'name': row['name'],
            'variant': row['variant'],
            'reason': row['reason'],
            'type': row['type'],
            'zoneAuthority': [auth],
        }
    }

    if useForDronechart:
        feature['properties'].update({
            "upper": row['upper'],
            "upperUom": f"{row['uom']} {row['upperRef']}",
            "lower": row['lower'],
            "lowerUom": f"{row['uom']} {row['lowerRef']}",
        })

    if row['restrictionConditions'] is not None:
        feature['properties']['restrictionConditions'] = row['restrictionConditions']
    if row['otherReasonInfo'] is not None:
        feature['properties']['otherReasonInfo'] = row['otherReasonInfo']
    if not pd.isna(row['regulationExemption']):
        feature['properties']['regulationExemption'] = row['regulationExemption']
    if row['message'] is not None:
        feature['properties']['message'] = row['message']
    if not pd.isna(row['extendedProperties']):
        feature['properties']['extendedProperties'] = {"text": row['extendedProperties']}

    if times is not None:
        feature['properties']['limitedApplicability'] = [times]

    return feature

# Funktion för att hämta WKT från en WFS-tjänst
def get_wkt_from_wfs(ids):
    wfs_url = "https://daim.lfv.se/geoserver/wfs"
    layers = ["RSTA", "DNGA", "CTR", "ATZ", "TIZ"]
    id_to_geometry = {}
    for layer in layers:
        filter_query = '<Or>' + ''.join([f"<PropertyIsEqualTo><PropertyName>NAMEOFAREA</PropertyName><Literal>{id}</Literal></PropertyIsEqualTo>" for id in ids]) + '</Or>'
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
            "filter": f"<Filter>{filter_query}</Filter>"
        }
        response = requests.get(wfs_url, params=params)
        geojson = response.json()
        for feature in geojson['features']:
            id_to_geometry[feature['properties']['NAMEOFAREA']] = feature['geometry']
        # Ta bort ID:n som redan hittats från listan
        ids = [id for id in ids if id not in id_to_geometry]
        if not ids:  # Om alla ID:n har hittats, bryt loopen
            break
    return id_to_geometry

# Streamlit UI
st.title('Excel to ED318 GeoJSON Converter')

provider = st.text_input("Provider", "Luftfartsverket, 601 79 Norrköping, utm@lfv.se")
issued = st.date_input("Issued", datetime(2025, 1, 30), key="issued")
validFrom = st.date_input("Valid From", datetime(2025, 3, 1), key="valid_from")
validTo = st.date_input("Valid To", None, key="valid_to")
description = st.text_input("Description", "", key="description")
technicalLimitation = st.text_input("Technical Limitation", "", key="limitations")
useForDronechart = st.checkbox("Use for Dronechart", False)

# File uploader
uploaded_file = st.file_uploader("Choose an Excel file", type=["xlsx"])

if uploaded_file is not None:
    if st.button('Convert'):

        issued_datetime = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_from_datetime = validFrom.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_to_datetime = validTo.strftime("%Y-%m-%dT%H:%M:%SZ") if validTo else ""

        df = pd.read_excel(uploaded_file)
        df['reason'] = df['reason'].fillna('')

        # Hämta alla ID:n som saknar geometri
        missing_geometry_ids = df[df['geometry'].isna()]['identifier'].tolist()

        if missing_geometry_ids:
            # Hämta WKT från WFS för alla saknade geometriska data
            id_to_geometry = get_wkt_from_wfs(missing_geometry_ids)
            # Uppdatera DataFrame med hämtad geometri
            for index, row in df.iterrows():
                if pd.isna(row['geometry']) and row['identifier'] in id_to_geometry:
                    df.at[index, 'geometry'] = id_to_geometry[row['identifier']]

        df.dropna(subset=['identifier', 'geometry'], inplace=True)
        df['name'] = df.apply(lambda row: create_language_list(row, "name_en", "name_se"), axis=1)
        df['authority_name'] = df.apply(lambda row: create_language_list(row, "authorityName_en", "authorityName_se", name_attr="name"), axis=1)
        df['restrictionConditions'] = df["restrictionConditions_en"]
        df['otherReasonInfo'] = df.apply(lambda row: create_language_list(row, "otherReasonInfo_en", "otherReasonInfo_se", name_attr="text"), axis=1)
        df['message'] = df.apply(lambda row: create_language_list(row, "message_en", "message_se", name_attr="text"), axis=1)
        df['reason'] = df['reason'].apply(lambda x: [reason.strip() for reason in x.split(',')])
        df['geojson_feature'] = df.apply(create_geojson_feature, axis=1)

        geojson_collection = {
            'type': 'FeatureCollection',
            'title': 'Sample GeoZOnes',
            'metadata': {
                'provider': [{'lang': 'en-GB', 'text': provider}],
                'issued': issued_datetime,
                'validFrom': valid_from_datetime,
                'technicalLimitations': [{'lang': 'en-GB', 'text': technicalLimitation}]
            },
            'features': list(df['geojson_feature'])
        }

        if valid_to_datetime != "":
            geojson_collection['metadata']['validTo'] = valid_to_datetime
        if description != "":
            geojson_collection['metadata']['description'] = [{'lang': 'en-GB', 'text': description}]

        geojson_path = 'zones_dronechart.json' if useForDronechart else 'uas_zones_ED318.json'
        with open(geojson_path, 'w', encoding='utf-8') as geojson_file:
            json.dump(geojson_collection, geojson_file, ensure_ascii=False, indent=4)

        st.success(f"GeoJSON file saved as {geojson_path}")

        with open(geojson_path, 'rb') as f:
            st.download_button(
                label="Download ED318 GeoJSON file",
                data=f,
                file_name=geojson_path,
                mime="application/json"
            )
