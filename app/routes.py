from __future__ import annotations
import json
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import geopandas as gpd
import pandas as pd
from flask import (
    jsonify, render_template, send_from_directory,
    request, Response, send_file, redirect, url_for, flash, current_app
)
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User, Project
from services.ibge import buscar_geometria_ibge
from services.segmentation import processar_segmentacao_completa
from services.features import build_features
from services.propagation import propagate_labels

BASE_DIR = Path(__file__).resolve().parents[1]
S2_DIR = BASE_DIR / "SENTINEL2_BANDAS"

def project_dir(project_id: int) -> Path:
    return S2_DIR / "projects" / str(int(project_id))

def _latest_propagado(project_id: int) -> Path | None:
    out_dir = project_dir(project_id)
    files = sorted(out_dir.glob("propagado_*.geojson"))
    return files[-1] if files else None

# --- ROTAS DE AUTENTICAÇÃO ---

@current_app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("home"))
        flash("Email ou senha inválidos.", "danger")
    return render_template("login.html")

@current_app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user = User(
            username=(request.form.get("username") or "").strip(),
            email=(request.form.get("email") or "").strip().lower()
        )
        user.set_password(request.form.get("password"))
        db.session.add(user)
        db.session.commit()
        flash("Conta criada com sucesso!", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@current_app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- GESTÃO DE PROJETOS ---

@current_app.route("/")
@login_required
def home():
    projetos = Project.query.filter_by(user_id=current_user.id).order_by(Project.date_created.desc()).all()
    return render_template("index.html", user=current_user, projetos=projetos)

@current_app.route("/projeto/excluir/<int:project_id>", methods=["POST"])
@login_required
def excluir_projeto(project_id: int):
    projeto = Project.query.get_or_404(project_id)
    if projeto.user_id != current_user.id:
        flash("Acesso negado.", "danger")
        return redirect(url_for("home"))
    try:
        out_dir = project_dir(project_id)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        db.session.delete(projeto)
        db.session.commit()
        flash(f"Projeto '{projeto.name}' removido.", "success")
    except Exception as e:
        flash(f"Erro ao excluir: {e}", "danger")
    return redirect(url_for("home"))

@current_app.route("/classification")
@login_required
def resultado():
    project_id = request.args.get("project_id", type=int)
    projeto = Project.query.get_or_404(project_id)
    if projeto.user_id != current_user.id:
        return redirect(url_for("home"))
    return render_template("classification.html", project_id=project_id, projeto=projeto)

# --- APIs DE DADOS E PROCESSAMENTO ---

@current_app.route("/api/segmentar", methods=["POST"])
@login_required
def api_segmentar():
    data = request.get_json() or {}
    bbox = data.get("bbox")
    nome_p = data.get("nome_projeto") or f"Projeto {datetime.now().strftime('%d/%m %H:%M')}"
    
    novo = Project(name=nome_p, bbox=json.dumps(bbox), user_id=current_user.id)
    db.session.add(novo)
    db.session.commit()

    out_dir = project_dir(novo.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        processar_segmentacao_completa(
            output_dir=str(out_dir), bbox=bbox, aoi_geojson=data.get("aoi_geojson"),
            algoritmo=data.get("algoritmo", "SLIC")
        )
        return jsonify({"status": "sucesso", "project_id": novo.id}), 200
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500

@current_app.route("/salvar_classificacao", methods=["POST"])
@login_required
def salvar_classificacao():
    data = request.get_json()
    project_id = int(data.get("project_id"))
    projeto = Project.query.get_or_404(project_id)
    if projeto.user_id != current_user.id: return jsonify({"erro": "Negado"}), 403
    
    out_dir = project_dir(project_id)
    gdf_amostras = gpd.GeoDataFrame.from_features(data.get("features"), crs="EPSG:4326")
    gdf_amostras.to_file(out_dir / "classificado.geojson", driver="GeoJSON")
    return jsonify({"status": "ok"})

@current_app.route("/api/propagate", methods=["POST"])
@login_required
def api_propagate():
    data = request.get_json()
    project_id = int(data.get("project_id"))
    projeto = Project.query.get_or_404(project_id)
    if projeto.user_id != current_user.id: return jsonify({"erro": "Negado"}), 403
    
    result = propagate_labels(method=data.get("method"), params=data.get("params"), output_dir=str(project_dir(project_id)))
    return jsonify(result)

# --- EXPORTAÇÃO MULTI-FORMATO (NOVO) ---

@current_app.route("/download/<int:project_id>/<filename>")
@login_required
def baixar_arquivo_projeto(project_id: int, filename: str):
    projeto = Project.query.get_or_404(project_id)
    if projeto.user_id != current_user.id: return "Acesso Negado", 403
    
    out_dir = project_dir(project_id)
    
    # Lógica de Conversão Dinâmica
    try:
        # 1. Exportar para CSV (Atributos)
        if filename == "features.csv":
            df = pd.read_parquet(out_dir / "features.parquet")
            csv_path = out_dir / "features.csv"
            df.to_csv(csv_path, index=False)
            return send_from_directory(str(out_dir), "features.csv", as_attachment=True)

        # 2. Exportar para SHAPEFILE (Amostras ou Resultado)
        if filename.endswith(".shp"):
            source_json = "classificado.geojson" if "amostras" in filename else "resultado_ultima_propagacao.geojson"
            if not (out_dir / source_json).exists(): return "Arquivo base não encontrado", 404
            
            gdf = gpd.read_file(out_dir / source_json)
            # Shapefile requer uma pasta ou ZIP pois são vários arquivos (.dbf, .shx, etc)
            temp_shp_dir = Path(tempfile.mkdtemp())
            shp_path = temp_shp_dir / filename
            gdf.to_file(shp_path)
            
            # Zipar para download
            zip_path = shutil.make_archive(str(temp_shp_dir / filename.replace(".shp", "")), 'zip', temp_shp_dir)
            return send_file(zip_path, as_attachment=True, download_name=filename.replace(".shp", ".zip"))

        # 3. Exportar TIF (Mapa Final - caso você tenha o rasterizado)
        # Se você ainda não tem a função de rasterizar o GeoJSON, ele baixará o RGB original como fallback
        if filename == "mapa_final.tif":
            original_tif = out_dir / "RGB_composicao_8bit.tif"
            return send_file(str(original_tif), as_attachment=True)

        # 4. Comportamento Padrão (GeoJSON, Parquet, TIFs de bandas)
        return send_from_directory(str(out_dir), filename, as_attachment=True)

    except Exception as e:
        return f"Erro na conversão: {str(e)}", 500

@current_app.route("/resultado_geojson")
@login_required
def resultado_geojson():
    project_id = request.args.get("project_id", type=int)
    out_dir = project_dir(project_id)
    files = list(out_dir.glob("segments*.geojson"))
    if not files: return jsonify({"erro": "Não encontrado"}), 404
    path = max(files, key=os.path.getmtime)
    gdf = gpd.read_file(path)
    return Response(gdf.to_json(), mimetype="application/json")

@current_app.route("/resultado_propagado")
@login_required
def resultado_propagado():
    project_id = request.args.get("project_id", type=int)
    fname = request.args.get("path")
    out_dir = project_dir(project_id)
    path = out_dir / fname if fname else _latest_propagado(project_id)
    if not path or not path.exists(): return jsonify({"erro": "Não encontrado"}), 404
    gdf = gpd.read_file(path)
    return Response(gdf.to_json(), mimetype="application/json")

@current_app.route("/bandas/<int:project_id>/<nome>")
@login_required
def servir_banda(project_id: int, nome: str):
    return send_from_directory(str(project_dir(project_id)), nome)