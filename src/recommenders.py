import pandas as pd
import numpy as np

# Для работы с матрицами
from scipy.sparse import csr_matrix

# Матричная факторизация
from implicit.als import AlternatingLeastSquares
from implicit.nearest_neighbours import ItemItemRecommender  # нужен для одного трюка
from implicit.nearest_neighbours import bm25_weight, tfidf_weight


class MainRecommender:

    def __init__(self, data, weighting=True):

        self.top_purchases = data.groupby(['user_id', 'item_id'])['quantity'].count().reset_index()
        self.top_purchases.sort_values('quantity', ascending=False, inplace=True)
        self.top_purchases = self.top_purchases[self.top_purchases['item_id'] != 999999]

        self.overall_top_purchases = data.groupby('item_id')['quantity'].count().reset_index()
        self.overall_top_purchases.sort_values('quantity', ascending=False, inplace=True)
        self.overall_top_purchases = self.overall_top_purchases[self.overall_top_purchases['item_id'] != 999999]
        self.overall_top_purchases = self.overall_top_purchases.item_id.tolist()
        self.user_item_matrix = self._prepare_matrix(data)  # pd.DataFrame
        self.id_to_itemid, self.id_to_userid, \
            self.itemid_to_id, self.userid_to_id = self._prepare_dicts(self.user_item_matrix)
        if weighting:
            self.user_item_matrix = bm25_weight(self.user_item_matrix.T).T
        self.model = self.fit(self.user_item_matrix)
        self.own_recommender = self.fit_own_recommender(self.user_item_matrix)
        self.item_factors = self.model.item_factors
        self.user_factors = self.model.user_factors
        self.items_emb_df, self.users_emb_df = self.get_embeddings(self)

    @staticmethod
    def get_embeddings(self):
        items_emb = self.item_factors
        items_emb_df = pd.DataFrame(items_emb)
        items_emb_df.reset_index(inplace=True)
        items_emb_df['item_id'] = items_emb_df['index'].apply(lambda x: self.id_to_itemid[x])
        items_emb_df = items_emb_df.drop('index', axis=1)
        users_emb = self.user_factors
        users_emb_df = pd.DataFrame(users_emb)
        users_emb_df.reset_index(inplace=True)
        users_emb_df['user_id'] = users_emb_df['index'].apply(lambda x: self.id_to_userid[x])
        users_emb_df = users_emb_df.drop('index', axis=1)
        return items_emb_df, users_emb_df

    @staticmethod
    def _prepare_matrix(data):
        user_item_matrix = pd.pivot_table(data,
                                          index='user_id', columns='item_id',
                                          values='quantity',  # Можно пробовать другие варианты
                                          aggfunc='count',
                                          fill_value=0
                                          )
        user_item_matrix = user_item_matrix.astype(float)  # необходимый тип матрицы для implicit
        return user_item_matrix

    @staticmethod
    def _prepare_dicts(user_item_matrix):
        userids = user_item_matrix.index.values
        itemids = user_item_matrix.columns.values
        matrix_userids = np.arange(len(userids))
        matrix_itemids = np.arange(len(itemids))
        id_to_itemid = dict(zip(matrix_itemids, itemids))
        id_to_userid = dict(zip(matrix_userids, userids))
        itemid_to_id = dict(zip(itemids, matrix_itemids))
        userid_to_id = dict(zip(userids, matrix_userids))
        return id_to_itemid, id_to_userid, itemid_to_id, userid_to_id

    @staticmethod
    def fit_own_recommender(user_item_matrix):
        own_recommender = ItemItemRecommender(K=1, num_threads=4)
        own_recommender.fit(csr_matrix(user_item_matrix).T.tocsr())
        return own_recommender

    @staticmethod
    def fit(user_item_matrix, n_factors=20, regularization=0.001, iterations=15, num_threads=4):
        model = AlternatingLeastSquares(factors=n_factors,
                                        regularization=regularization,
                                        iterations=iterations,
                                        num_threads=num_threads)
        model.fit(csr_matrix(user_item_matrix).T.tocsr())
        return model

    def _update_dict(self, user_id):
        if user_id not in self.userid_to_id.keys():
            max_id = max(list(self.userid_to_id.values()))
            max_id += 1
            self.userid_to_id.update({user_id: max_id})
            self.id_to_userid.update({max_id: user_id})

    def _get_similar_item(self, item_id):
        recs = self.model.similar_items(self.itemid_to_id[item_id], N=2)  # Товар похож на себя -> рекомендуем 2 товара
        top_rec = recs[1][0]  # И берем второй (не товар из аргумента метода)
        return self.id_to_itemid[top_rec]

    def _extend_with_top_popular(self, recommendations, N=5):
        if len(recommendations) < N:
            recommendations.extend(self.overall_top_purchases[:N])
            recommendations = recommendations[:N]
        return recommendations

    def _get_recommendations(self, user, model, N=5):
        try:
            res = [self.id_to_itemid[rec[0]] for rec in
                   model.recommend(userid=self.userid_to_id[user],
                                   user_items=csr_matrix(self.user_item_matrix).tocsr(),  # на вход user-item matrix
                                   N=N,
                                   filter_already_liked_items=False,
                                   filter_items=[self.itemid_to_id[999999]],
                                   recalculate_user=True)]
            res = self._extend_with_top_popular(res, N=N)
        except:
            res = self.overall_top_purchases[:N]
        return res

    def get_als_recommendations(self, user, N=5):
        self._update_dict(user_id=user)
        return self._get_recommendations(user, model=self.model, N=N)

    def get_own_recommendations(self, user, N=5):
        self._update_dict(user_id=user)
        return self._get_recommendations(user, model=self.own_recommender, N=N)

    def get_similar_items_recommendation(self, user, N=5):
        top_users_purchases = self.top_purchases[self.top_purchases['user_id'] == user].head(N)
        res = top_users_purchases['item_id'].apply(lambda x: self._get_similar_item(x)).tolist()
        res = self._extend_with_top_popular(res, N=N)
        assert len(res) == N, 'Количество рекомендаций != {}'.format(N)
        return res

    def get_similar_users_recommendation(self, user, N=5):
        res = []
        self._update_dict(user_id=user)
        try:
            similar_users = self.model.similar_users(self.userid_to_id[user], N=N + 1)
            similar_users = [rec_usr[0] for rec_usr in similar_users]
            similar_users = similar_users[1:]
            for usr in similar_users:
                res.extend(self.get_own_recommendations(self.id_to_userid[usr], N=1))
            res = self._extend_with_top_popular(res, N=N)
        except:
            res = self.overall_top_purchases[:N]
        assert len(res) == N, 'Количество рекомендаций != {}'.format(N)
        return res
