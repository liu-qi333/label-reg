import tensorflow as tf



def build_loss(similarity_type, similarity_scales, regulariser_type, regulariser_weight,
               label_moving, label_fixed, network_type, ddf, grid):
    label_similarity = multi_scale_loss(label_fixed, label_moving, similarity_type.lower(), grid, similarity_scales)
    if network_type.lower() == 'global':
        ddf_regularisation = tf.constant(0.0)
    else:
        ddf_regularisation = tf.reduce_mean(local_displacement_energy(ddf, regulariser_type, regulariser_weight))
    return tf.reduce_mean(label_similarity), ddf_regularisation


def weighted_binary_cross_entropy(ts, ps, pw=1, eps=1e-6):
    ps = tf.clip_by_value(ps, eps, 1-eps)
    return -tf.reduce_sum(
        tf.concat([ts*pw, 1-ts], axis=4)*tf.log(tf.concat([ps, 1-ps], axis=4)),
        axis=4, keep_dims=True)


def dice_simple(ts, ps, eps_vol=1e-6):
    numerator = tf.reduce_sum(ts*ps, axis=[1, 2, 3, 4]) * 2
    denominator = tf.reduce_sum(ts, axis=[1, 2, 3, 4]) + tf.reduce_sum(ps, axis=[1, 2, 3, 4])+eps_vol
    return numerator/denominator


def dice_generalised(ts, ps, weights):
    ts2 = tf.concat([ts, 1-ts], axis=4)
    ps2 = tf.concat([ps, 1-ps], axis=4)
    numerator = 2 * tf.reduce_sum(tf.reduce_sum(ts2*ps2, axis=[1, 2, 3]) * weights, axis=1)
    denominator = tf.reduce_sum((tf.reduce_sum(ts2, axis=[1, 2, 3]) +
                                 tf.reduce_sum(ps2, axis=[1, 2, 3])) * weights, axis=1)
    return numerator/denominator


def jaccard_simple(ts, ps, eps_vol=1e-6):
    numerator = tf.reduce_sum(ts*ps, axis=[1, 2, 3, 4])
    denominator = tf.reduce_sum(tf.square(ts), axis=[1, 2, 3, 4]) + \
                  tf.reduce_sum(tf.square(ps), axis=[1, 2, 3, 4]) - numerator + eps_vol
    return numerator/denominator


def gauss_kernel1d(sigma):
    if sigma == 0:
        return 0
    else:
        tail = int(sigma*3)
        k = tf.exp([-0.5*x**2/sigma**2 for x in range(-tail, tail+1)])
        return k / tf.reduce_sum(k)


def cauchy_kernel1d(sigma):  # this is an approximation
    if sigma == 0:
        return 0
    else:
        tail = int(sigma*5)
        # k = tf.reciprocal(([((x/sigma)**2+1)*sigma*3.141592653589793 for x in range(-tail, tail+1)]))
        k = tf.reciprocal([((x/sigma)**2+1) for x in range(-tail, tail + 1)])
        return k / tf.reduce_sum(k)


def separable_filter3d(vol, kernel):
    if kernel == 0:
        return vol
    else:
        strides = [1, 1, 1, 1, 1]
        return tf.nn.conv3d(tf.nn.conv3d(tf.nn.conv3d(
            vol,
            tf.reshape(kernel, [-1, 1, 1, 1, 1]), strides, "SAME"),
            tf.reshape(kernel, [1, -1, 1, 1, 1]), strides, "SAME"),
            tf.reshape(kernel, [1, 1, -1, 1, 1]), strides, "SAME")


def centre_distance(ts, ps, grid, eps_overlap=1e-6):
    m1 = tf.reduce_sum(ts, axis=[1, 2, 3, 4])+eps_overlap
    m2 = tf.reduce_sum(ps, axis=[1, 2, 3, 4])+eps_overlap
    c1 = tf.reduce_sum(ts * tf.expand_dims(grid, axis=0), axis=[1, 2, 3, 4]) / m1
    c2 = tf.reduce_sum(ps * tf.expand_dims(grid, axis=0), axis=[1, 2, 3, 4]) / m2
    return tf.sqrt(tf.reduce_sum(tf.square(c1-c2), axis=0, keep_dims=True))


def single_scale_loss(label_fixed, label_moving, loss_type, grid):
    if loss_type == 'cross-entropy':
        label_loss_batch = tf.reduce_mean(weighted_binary_cross_entropy(label_fixed, label_moving), axis=[1, 2, 3, 4])
    elif loss_type == 'mean-squared':
        label_loss_batch = tf.reduce_mean(tf.squared_difference(label_fixed, label_moving), axis=[1, 2, 3, 4])
    elif loss_type == 'dice':
        label_loss_batch = 1 - dice_simple(label_fixed, label_moving)
    elif loss_type == 'jaccard':
        label_loss_batch = 1 - jaccard_simple(label_fixed, label_moving)
    elif loss_type == 'centre-distance':  # experimental
        label_loss_batch = centre_distance(label_fixed, label_moving, grid)
    else:
        raise Exception('Not recognised label correspondence loss!')
    return label_loss_batch


def multi_scale_loss(label_fixed, label_moving, loss_type, grid, loss_scales):
    label_loss_all = tf.stack(
        [single_scale_loss(
            separable_filter3d(label_fixed, gauss_kernel1d(s)),
            separable_filter3d(label_moving, gauss_kernel1d(s)), loss_type, grid)
            for s in loss_scales],
        axis=1)
    return tf.reduce_mean(label_loss_all, axis=1)


def local_displacement_energy(ddf, energy_type, energy_weight):

    def gradient_dx(fv): return (fv[:, 2:, 1:-1, 1:-1] - fv[:, :-2, 1:-1, 1:-1]) / 2

    def gradient_dy(fv): return (fv[:, 1:-1, 2:, 1:-1] - fv[:, 1:-1, :-2, 1:-1]) / 2

    def gradient_dz(fv): return (fv[:, 1:-1, 1:-1, 2:] - fv[:, 1:-1, 1:-1, :-2]) / 2

    def gradient_txyz(Txyz, fn):
        return tf.stack([fn(Txyz[..., i]) for i in [0, 1, 2]], axis=4)

    def compute_gradient_norm(displacement, flag_l1=False):
        dTdx = gradient_txyz(displacement, gradient_dx)
        dTdy = gradient_txyz(displacement, gradient_dy)
        dTdz = gradient_txyz(displacement, gradient_dz)
        if flag_l1:
            norms = tf.abs(dTdx) + tf.abs(dTdy) + tf.abs(dTdz)
        else:
            norms = dTdx**2 + dTdy**2 + dTdz**2
        return tf.reduce_mean(norms, [1, 2, 3, 4])

    def compute_bending_energy(displacement):
        dTdx = gradient_txyz(displacement, gradient_dx)
        dTdy = gradient_txyz(displacement, gradient_dy)
        dTdz = gradient_txyz(displacement, gradient_dz)
        dTdxx = gradient_txyz(dTdx, gradient_dx)
        dTdyy = gradient_txyz(dTdy, gradient_dy)
        dTdzz = gradient_txyz(dTdz, gradient_dz)
        dTdxy = gradient_txyz(dTdx, gradient_dy)
        dTdyz = gradient_txyz(dTdy, gradient_dz)
        dTdxz = gradient_txyz(dTdx, gradient_dz)
        return tf.reduce_mean(dTdxx**2 + dTdyy**2 + dTdzz**2 + 2*dTdxy**2 + 2*dTdxz**2 + 2*dTdyz**2, [1, 2, 3, 4])

    if energy_weight:
        if energy_type == 'bending':
            energy = compute_bending_energy(ddf)
        elif energy_type == 'gradient-l2':
            energy = compute_gradient_norm(ddf)
        elif energy_type == 'gradient-l1':
            energy = compute_gradient_norm(ddf, flag_l1=True)
        else:
            raise Exception('Not recognised local regulariser!')
    else:
        energy = tf.constant(0.0)

    return energy*energy_weight
